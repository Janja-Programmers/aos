"""Frappe-side orchestration for the external analytics pipeline.

Frappe remains the source of truth for product, user, permission, and feature
events. The external analytics-pipeline service owns fast ingestion, Redis
streams/counters, and future aggregation/export work.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import frappe
import requests
from frappe.utils import now_datetime

from aos.services.transactional_outbox import (
	OutboxConflictError,
	complete_outbox_without_callback,
	current_outbox_dispatch_context,
	ensure_outbox_for_job,
	mark_outbox_callback,
	outbox_dispatch_context,
	record_companion_dispatch_outcome,
	sanitized_dispatch_error,
	validate_callback_idempotency,
)
from aos.utils.aos_config import clean_url, get_env, get_env_bool, get_env_int, get_first_env


class AnalyticsPipelineError(RuntimeError):
	"""Raised when analytics pipeline orchestration fails."""


@dataclass(frozen=True)
class AnalyticsPipelineConfig:
	service_url: str
	service_secret: str = field(repr=False)
	callback_secret: str = field(repr=False)
	callback_url: str
	request_timeout_seconds: int
	max_attempts: int
	queue: str
	dispatcher_timeout_seconds: int
	enabled: bool
	fail_open: bool
	max_events_per_job: int


def get_analytics_pipeline_config() -> AnalyticsPipelineConfig:
	service_url = clean_url(
		get_first_env(
			"ANALYTICS_SERVICE_URL",
			default=f"http://127.0.0.1:{get_env('ANALYTICS_SERVICE_PORT', '8170')}",
		),
		default="http://127.0.0.1:8170",
	)
	callback_url = clean_url(get_env("ANALYTICS_CALLBACK_URL"))
	if not callback_url:
		domain = get_env("AOS_API_DOMAIN")
		if domain:
			callback_url = f"https://{domain}/api/method/aos.api.v1.analytics_pipeline.handle_callback"
		else:
			callback_url = "http://127.0.0.1:8000/api/method/aos.api.v1.analytics_pipeline.handle_callback"

	return AnalyticsPipelineConfig(
		service_url=service_url,
		service_secret=get_env("ANALYTICS_SERVICE_SECRET", "") or "",
		callback_secret=get_env("ANALYTICS_SERVICE_CALLBACK_SECRET", "") or "",
		callback_url=callback_url,
		request_timeout_seconds=get_env_int(
			"ANALYTICS_SERVICE_REQUEST_TIMEOUT_SECONDS", 20, min_value=5, max_value=120
		),
		max_attempts=get_env_int("ANALYTICS_MAX_RETRIES", 3, min_value=1, max_value=10),
		queue=get_env("ANALYTICS_FRAPPE_QUEUE", "long") or "long",
		dispatcher_timeout_seconds=get_env_int(
			"ANALYTICS_DISPATCHER_TIMEOUT_SECONDS", 300, min_value=60, max_value=1800
		),
		enabled=get_env_bool("ANALYTICS_PIPELINE_ENABLED", True),
		fail_open=get_env_bool("ANALYTICS_PIPELINE_FAIL_OPEN", True),
		max_events_per_job=get_env_int("ANALYTICS_MAX_EVENTS_PER_JOB", 200, min_value=1, max_value=1000),
	)


def _json_bytes(payload: dict[str, Any]) -> bytes:
	return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def build_signature(secret: str, payload: bytes) -> str:
	digest = hmac.new(str(secret or "").encode("utf-8"), payload, hashlib.sha256).hexdigest()
	return f"sha256={digest}"


def verify_signature(secret: str, payload: bytes, signature: str | None) -> bool:
	if not str(secret or "").strip():
		return False
	if not signature:
		return False
	return hmac.compare_digest(build_signature(secret, payload), str(signature).strip())


def _json_dumps(value: Any) -> str:
	return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any):
	if not value:
		return default
	try:
		return json.loads(value)
	except Exception:
		return default


def _save_analytics_job(job, *, commit: bool = True) -> object:
	"""Persist an analytics job without revalidating historical link fields.

	Analytics events are historical and may point to users/documents that are
	deleted after the event was queued but before the dispatcher/callback runs.
	Link validation should not make background dispatch fail for those stale
	references; the immutable event payload still carries the original IDs.
	"""
	job.flags.ignore_links = True
	job.save(ignore_permissions=True)
	if commit:
		frappe.db.commit()
	return job


def _clean(value: Any, *, max_len: int = 180) -> str:
	text = str(value or "").strip()
	if len(text) > max_len:
		text = text[:max_len]
	return text


def _normalize_event(event: dict[str, Any]) -> dict[str, Any] | None:
	if not isinstance(event, dict):
		return None
	event_type = _clean(event.get("event_type"), max_len=120)
	if not event_type:
		return None

	metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
	metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}

	return {
		"event_type": event_type,
		"event_group": _clean(event.get("event_group") or event.get("group"), max_len=80),
		"user": _clean(event.get("user"), max_len=180),
		"session_id": _clean(event.get("session_id"), max_len=180),
		"source": _clean(event.get("source"), max_len=120),
		"platform": _clean(event.get("platform"), max_len=40),
		"country": _clean(event.get("country"), max_len=80),
		"target_doctype": _clean(event.get("target_doctype"), max_len=120),
		"target_name": _clean(event.get("target_name"), max_len=180),
		"route_type": _clean(event.get("route_type"), max_len=80),
		"route_id": _clean(event.get("route_id"), max_len=180),
		"occurred_at": _clean(event.get("occurred_at"), max_len=80) or str(now_datetime()),
		"metadata": metadata,
		"metrics": metrics,
	}


def create_analytics_ingest_job(
	*,
	events: list[dict[str, Any]],
	source: str = "server",
	enqueue: bool = True,
) -> object | None:
	config = get_analytics_pipeline_config()
	if not config.enabled:
		return None

	normalized: list[dict[str, Any]] = []
	for event in events[: config.max_events_per_job]:
		item = _normalize_event(event)
		if item:
			normalized.append(item)

	if not normalized:
		if config.fail_open:
			return None
		raise AnalyticsPipelineError("At least one valid analytics event is required")

	first = normalized[0]
	job = frappe.get_doc(
		{
			"doctype": "AOS Analytics Ingest Job",
			"source": _clean(source, max_len=120) or "server",
			"event_group": first.get("event_group"),
			"event_type": first.get("event_type"),
			"user": first.get("user"),
			"session_id": first.get("session_id"),
			"target_doctype": first.get("target_doctype"),
			"target_name": first.get("target_name"),
			"status": "Queued",
			"attempt_count": 0,
			"max_attempts": config.max_attempts,
			"idempotency_key": uuid.uuid4().hex,
			"event_count": len(normalized),
			"events_json": _json_dumps(normalized),
		}
	)
	job.insert(ignore_permissions=True)

	if enqueue:
		enqueue_analytics_ingest_dispatch(job.name)

	return job


def emit_analytics_event(**event: Any) -> object | None:
	"""Best-effort helper for feature APIs.

	This should never break the primary user action when fail-open is enabled.
	"""
	config = get_analytics_pipeline_config()
	try:
		return create_analytics_ingest_job(
			events=[event],
			source=_clean(event.get("source"), max_len=120) or "server",
			enqueue=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "AOS Analytics Event Emit Failed")
		if config.fail_open:
			return None
		raise


def enqueue_analytics_ingest_dispatch(analytics_job_id: str) -> object:
	config = get_analytics_pipeline_config()
	job = frappe.get_doc("AOS Analytics Ingest Job", analytics_job_id)
	return ensure_outbox_for_job(
		service_type="analytics_ingestion",
		job=job,
		queue=config.queue,
		timeout_seconds=config.dispatcher_timeout_seconds,
		aggregate_doctype=job.target_doctype or None,
		aggregate_name=job.target_name or None,
		max_attempts=job.max_attempts,
	)


def build_analytics_ingest_payload(job) -> dict[str, Any]:
	dispatch_context = outbox_dispatch_context(job_doctype="AOS Analytics Ingest Job", job_name=job.name)
	return {
		**dispatch_context,
		"job_id": job.name,
		"idempotency_key": job.idempotency_key,
		"source": job.source,
		"events": _json_loads(job.events_json, []),
		"callback_url": get_analytics_pipeline_config().callback_url,
	}


def dispatch_analytics_ingest_job(analytics_job_id: str) -> object:
	job = frappe.get_doc("AOS Analytics Ingest Job", analytics_job_id)
	dispatch_context = current_outbox_dispatch_context(
		job_doctype="AOS Analytics Ingest Job", job_name=job.name
	)
	if job.status in {"Ingested", "Skipped", "Cancelled"}:
		return job
	if (
		job.status == "Processing"
		and getattr(job, "service_job_id", None)
		and not (dispatch_context and dispatch_context.recovery_dispatch)
	):
		return job

	config = get_analytics_pipeline_config()
	if not config.enabled:
		job.status = "Cancelled"
		job.last_error = "Analytics pipeline is disabled"
		job.completed_at = now_datetime()
		job.flags.ignore_links = True
		job.save(ignore_permissions=True)
		complete_outbox_without_callback(
			job_doctype="AOS Analytics Ingest Job",
			job_name=job.name,
			status="cancelled",
		)
		frappe.db.commit()
		return job

	previous_work_attempt_count = int(job.attempt_count or 0)
	job.status = "Dispatching"
	job.last_error = None
	job.dispatched_at = now_datetime()
	_save_analytics_job(job)

	payload = build_analytics_ingest_payload(job)
	job.request_payload = _json_dumps(payload)
	_save_analytics_job(job)

	body = _json_bytes(payload)
	headers = {
		"Content-Type": "application/json",
		"X-AOS-Analytics-Signature": build_signature(config.service_secret, body),
		"Idempotency-Key": job.idempotency_key,
	}
	dispatch_action = ""

	try:
		response = requests.post(
			f"{config.service_url}/events",
			data=body,
			headers=headers,
			timeout=config.request_timeout_seconds,
		)
		response.raise_for_status()
		response_payload = response.json()
		dispatch_action = record_companion_dispatch_outcome(str(response_payload.get("dispatch_action") or ""), response_payload)

		job.service_job_id = (
			_clean(response_payload.get("service_job_id"))
			or _clean(response_payload.get("job_id"))
			or _clean(job.service_job_id)
		)
		if dispatch_action in {"enqueued", "stale_generation_replaced"}:
			job.attempt_count = previous_work_attempt_count + 1
		else:
			job.attempt_count = previous_work_attempt_count
		job.status = "Processing"
		job.started_at = now_datetime()
		job.response_payload = _json_dumps(response_payload)
		job.last_error = None
		_save_analytics_job(job)
		return job

	except OutboxConflictError as exc:
		job.reload()
		if dispatch_action in {"enqueued", "stale_generation_replaced"}:
			job.attempt_count = previous_work_attempt_count + 1
		else:
			job.attempt_count = previous_work_attempt_count
		job.status = "Processing"
		job.last_error = exc.error_code
		job.save(ignore_permissions=True)
		frappe.db.commit()
		raise
	except Exception as exc:
		error_code = sanitized_dispatch_error(exc)
		frappe.log_error(frappe.get_traceback(), f"Analytics dispatch failed: {error_code}")
		job.reload()
		# A transport error may occur after the companion accepted the stable job.
		# Keep business work nonterminal; the outbox reconciles by stable identity.
		job.status = "Processing"
		job.last_error = error_code
		job.save(ignore_permissions=True)
		frappe.db.commit()
		raise


def handle_analytics_ingest_callback(payload: dict[str, Any]) -> object:
	job_id = _clean(payload.get("job_id"))
	if not job_id:
		raise AnalyticsPipelineError("job_id is required")
	if not frappe.db.exists("AOS Analytics Ingest Job", job_id):
		raise AnalyticsPipelineError("Analytics ingest job not found")

	job = frappe.get_doc("AOS Analytics Ingest Job", job_id)
	incoming_status = _clean(payload.get("status")).lower()
	canonical_status = {"completed": "ingested", "ready": "ingested"}.get(incoming_status, incoming_status)
	validation = validate_callback_idempotency(job, payload, callback_status=canonical_status)
	if validation.duplicate:
		return job

	terminal_statuses = {"Ingested", "Skipped", "Failed"}
	if job.status in terminal_statuses:
		if job.status == "Ingested" and incoming_status in {"ingested", "completed", "ready"}:
			mark_outbox_callback(
				job_doctype="AOS Analytics Ingest Job",
				job_name=job.name,
				callback_status="ingested",
				success=True,
			)
			return job
		if job.status == "Skipped" and incoming_status == "skipped":
			mark_outbox_callback(
				job_doctype="AOS Analytics Ingest Job",
				job_name=job.name,
				callback_status="skipped",
				success=True,
			)
			return job
		if job.status == "Failed" and incoming_status == "failed":
			mark_outbox_callback(
				job_doctype="AOS Analytics Ingest Job",
				job_name=job.name,
				callback_status="failed",
				success=False,
				error=_clean(payload.get("error"), max_len=1000) or "Analytics ingest failed",
			)
			return job
		raise AnalyticsPipelineError(f"Analytics ingest job is already {job.status}")

	job.response_payload = _json_dumps(payload)
	job.callback_received_at = now_datetime()
	job.ingested_count = int(payload.get("ingested_count") or 0)
	job.skipped_count = int(payload.get("skipped_count") or 0)
	job.counters_json = _json_dumps(payload.get("counters") or {})

	if incoming_status in {"ingested", "completed", "ready"}:
		job.status = "Ingested"
		job.completed_at = now_datetime()
		job.last_error = None
		job.flags.ignore_links = True
		job.save(ignore_permissions=True)
		mark_outbox_callback(
			job_doctype="AOS Analytics Ingest Job",
			job_name=job.name,
			callback_status="ingested",
			success=True,
		)
		return job

	if incoming_status == "skipped":
		job.status = "Skipped"
		job.completed_at = now_datetime()
		job.last_error = None
		job.flags.ignore_links = True
		job.save(ignore_permissions=True)
		mark_outbox_callback(
			job_doctype="AOS Analytics Ingest Job",
			job_name=job.name,
			callback_status="skipped",
			success=True,
		)
		return job

	if incoming_status == "failed":
		return mark_analytics_ingest_job_failed(
			job.name,
			_clean(payload.get("error")) or "Analytics ingest failed",
			response_payload=payload,
			commit=False,
		)

	raise AnalyticsPipelineError("Invalid analytics callback status")


def mark_analytics_ingest_job_failed(
	job_id: str,
	error: str,
	*,
	response_payload: dict | None = None,
	commit: bool = True,
	dispatch_failure: bool = False,
) -> object:
	job = frappe.get_doc("AOS Analytics Ingest Job", job_id)
	job.status = "Failed"
	job.last_error = _clean(error, max_len=1000) or "Analytics ingest failed"
	if response_payload is not None:
		job.response_payload = _json_dumps(response_payload)
		job.callback_received_at = now_datetime()
	if int(job.attempt_count or 0) >= int(job.max_attempts or 3):
		job.completed_at = now_datetime()
	job.flags.ignore_links = True
	job.save(ignore_permissions=True)
	if not dispatch_failure:
		mark_outbox_callback(
			job_doctype="AOS Analytics Ingest Job",
			job_name=job.name,
			callback_status="failed",
			success=False,
			error=job.last_error,
		)
	if commit:
		frappe.db.commit()
	return job
