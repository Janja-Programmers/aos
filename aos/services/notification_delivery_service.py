"""Frappe-side orchestration for external notification delivery.

Frappe owns notification records, push tokens, permissions, and delivery-job
metadata. The external notification-delivery service owns provider calls,
retries inside its own queue, and per-token provider response handling.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
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


class NotificationDeliveryError(RuntimeError):
	"""Raised when notification-delivery orchestration fails."""


@dataclass(frozen=True)
class NotificationDeliveryConfig:
	service_url: str
	service_secret: str
	callback_secret: str
	callback_url: str
	request_timeout_seconds: int
	max_attempts: int
	queue: str
	dispatcher_timeout_seconds: int
	enabled: bool
	fail_open: bool


def get_notification_delivery_config() -> NotificationDeliveryConfig:
	service_url = clean_url(
		get_first_env(
			"NOTIFICATION_SERVICE_URL",
			default=f"http://127.0.0.1:{get_env('NOTIFICATION_SERVICE_PORT', '8160')}",
		),
		default="http://127.0.0.1:8160",
	)
	callback_url = clean_url(get_env("NOTIFICATION_CALLBACK_URL"))
	if not callback_url:
		domain = get_env("AOS_API_DOMAIN")
		if domain:
			callback_url = f"https://{domain}/api/method/aos.api.v1.notification_delivery.handle_callback"
		else:
			callback_url = "http://127.0.0.1:8000/api/method/aos.api.v1.notification_delivery.handle_callback"

	return NotificationDeliveryConfig(
		service_url=service_url,
		service_secret=get_env("NOTIFICATION_SERVICE_SECRET", "") or "",
		callback_secret=get_env("NOTIFICATION_SERVICE_CALLBACK_SECRET", "") or "",
		callback_url=callback_url,
		request_timeout_seconds=get_env_int(
			"NOTIFICATION_SERVICE_REQUEST_TIMEOUT_SECONDS", 20, min_value=5, max_value=120
		),
		max_attempts=get_env_int("NOTIFICATION_MAX_RETRIES", 3, min_value=1, max_value=10),
		queue=get_env("NOTIFICATION_FRAPPE_QUEUE", "long") or "long",
		dispatcher_timeout_seconds=get_env_int(
			"NOTIFICATION_DISPATCHER_TIMEOUT_SECONDS", 300, min_value=60, max_value=1800
		),
		enabled=get_env_bool("NOTIFICATION_DELIVERY_ENABLED", True),
		fail_open=get_env_bool("NOTIFICATION_DELIVERY_FAIL_OPEN", True),
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


def _clean(value: Any) -> str:
	return str(value or "").strip()


def _stringify_data(data: dict | None) -> dict[str, str]:
	return {str(k): str(v) for k, v in (data or {}).items() if v is not None}


def _get_active_push_tokens(user: str) -> list[dict[str, str]]:
	if not user:
		return []

	rows = frappe.get_all(
		"AOS Push Token",
		filters={"user": user, "is_active": 1},
		fields=["token", "token_hash", "device_type"],
	)

	deduped: dict[str, dict[str, str]] = {}
	for row in rows:
		token = _clean(row.get("token"))
		token_hash = _clean(row.get("token_hash"))
		if not token or not token_hash:
			continue
		deduped[token_hash] = {
			"token": token,
			"token_hash": token_hash,
			"device_type": _clean(row.get("device_type")) or "android",
		}

	return list(deduped.values())


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
	sanitized = dict(payload)
	tokens = sanitized.get("tokens") or []
	sanitized["tokens"] = [
		{
			"token_hash": token.get("token_hash"),
			"device_type": token.get("device_type"),
		}
		for token in tokens
		if isinstance(token, dict)
	]
	return sanitized


def create_notification_delivery_job(
	*,
	user: str,
	event: str,
	title: str,
	body: str,
	payload: dict | None = None,
	notification_id: str | None = None,
	delivery_kind: str = "persistent",
	priority: str | None = None,
	ttl_seconds: int | None = None,
	android_channel_id: str | None = None,
	android_notification_priority: str | None = None,
	enqueue: bool = True,
) -> object | None:
	config = get_notification_delivery_config()
	if not config.enabled:
		return None

	user = _clean(user)
	if not user:
		raise NotificationDeliveryError("Delivery user is required")

	data_payload = dict(payload or {})
	if event:
		data_payload["event"] = event

	job = frappe.get_doc(
		{
			"doctype": "AOS Notification Delivery Job",
			"user": user,
			"notification": notification_id,
			"delivery_kind": delivery_kind or "persistent",
			"channel": "push",
			"event": event,
			"title": title,
			"body": body,
			"priority": priority,
			"ttl_seconds": ttl_seconds,
			"android_channel_id": android_channel_id,
			"android_notification_priority": android_notification_priority,
			"status": "Queued",
			"attempt_count": 0,
			"max_attempts": config.max_attempts,
			"idempotency_key": uuid.uuid4().hex,
			"payload_json": _json_dumps(_stringify_data(data_payload)),
		}
	)
	job.insert(ignore_permissions=True)

	if enqueue:
		enqueue_notification_delivery_dispatch(job.name)

	return job


def enqueue_notification_delivery_dispatch(delivery_job_id: str) -> object:
	config = get_notification_delivery_config()
	job = frappe.get_doc("AOS Notification Delivery Job", delivery_job_id)
	return ensure_outbox_for_job(
		service_type="notification_delivery",
		job=job,
		queue=config.queue,
		timeout_seconds=config.dispatcher_timeout_seconds,
		aggregate_doctype="AOS Notification" if job.notification else "User",
		aggregate_name=job.notification or job.user,
		max_attempts=job.max_attempts,
	)


def build_notification_delivery_payload(job) -> dict[str, Any]:
	dispatch_context = outbox_dispatch_context(job_doctype="AOS Notification Delivery Job", job_name=job.name)
	tokens = _get_active_push_tokens(job.user)
	return {
		**dispatch_context,
		"job_id": job.name,
		"idempotency_key": job.idempotency_key,
		"notification_id": job.notification,
		"delivery_kind": job.delivery_kind,
		"channel": job.channel,
		"user": job.user,
		"event": job.event,
		"title": job.title,
		"body": job.body,
		"data": _json_loads(job.payload_json, {}),
		"options": {
			"priority": job.priority,
			"ttl_seconds": job.ttl_seconds,
			"android_channel_id": job.android_channel_id,
			"android_notification_priority": job.android_notification_priority,
		},
		"tokens": tokens,
		"callback_url": get_notification_delivery_config().callback_url,
	}


def dispatch_notification_delivery_job(delivery_job_id: str) -> object:
	job = frappe.get_doc("AOS Notification Delivery Job", delivery_job_id)
	dispatch_context = current_outbox_dispatch_context(
		job_doctype="AOS Notification Delivery Job", job_name=job.name
	)
	if job.status in {"Delivered", "Skipped", "Cancelled"}:
		return job
	if (
		job.status == "Processing"
		and getattr(job, "service_job_id", None)
		and not (dispatch_context and dispatch_context.recovery_dispatch)
	):
		return job

	config = get_notification_delivery_config()
	if not config.enabled:
		job.status = "Cancelled"
		job.last_error = "Notification delivery is disabled"
		job.completed_at = now_datetime()
		job.save(ignore_permissions=True)
		complete_outbox_without_callback(
			job_doctype="AOS Notification Delivery Job",
			job_name=job.name,
			status="cancelled",
		)
		frappe.db.commit()
		return job

	previous_work_attempt_count = int(job.attempt_count or 0)
	job.status = "Dispatching"
	job.last_error = None
	job.dispatched_at = now_datetime()
	job.save(ignore_permissions=True)
	frappe.db.commit()

	payload = build_notification_delivery_payload(job)
	token_count = len(payload.get("tokens") or [])
	job.token_count = token_count
	job.request_payload = json.dumps(_sanitize_payload(payload), ensure_ascii=False, default=str)
	job.save(ignore_permissions=True)
	frappe.db.commit()

	if token_count <= 0:
		job.status = "Skipped"
		job.success_count = 0
		job.failure_count = 0
		job.inactive_count = 0
		job.completed_at = now_datetime()
		job.last_error = None
		job.save(ignore_permissions=True)
		complete_outbox_without_callback(
			job_doctype="AOS Notification Delivery Job",
			job_name=job.name,
			status="skipped",
		)
		frappe.db.commit()
		return job

	body = _json_bytes(payload)
	headers = {
		"Content-Type": "application/json",
		"X-AOS-Notification-Signature": build_signature(config.service_secret, body),
		"Idempotency-Key": job.idempotency_key,
	}

	try:
		response = requests.post(
			f"{config.service_url}/jobs",
			data=body,
			headers=headers,
			timeout=config.request_timeout_seconds,
		)
		response.raise_for_status()
		data = response.json() if response.content else {}
		dispatch_action = record_companion_dispatch_outcome(str(data.get("dispatch_action") or ""), data)
		job.reload()
		if dispatch_action in {"enqueued", "stale_generation_replaced"}:
			job.attempt_count = previous_work_attempt_count + 1
		else:
			job.attempt_count = previous_work_attempt_count
		job.status = "Processing"
		job.service_job_id = str(data.get("service_job_id") or data.get("job_id") or job.service_job_id or "")
		job.started_at = now_datetime()
		job.save(ignore_permissions=True)
		frappe.db.commit()
		return job
	except OutboxConflictError as exc:
		job.reload()
		job.status = "Processing"
		job.last_error = exc.error_code
		job.save(ignore_permissions=True)
		frappe.db.commit()
		raise
	except Exception as exc:
		error_code = sanitized_dispatch_error(exc)
		frappe.log_error(frappe.get_traceback(), f"Notification dispatch failed: {error_code}")
		job.reload()
		# A transport error may occur after the companion accepted the stable job.
		# Keep business work nonterminal; the outbox reconciles by stable identity.
		job.status = "Processing"
		job.last_error = error_code
		job.save(ignore_permissions=True)
		frappe.db.commit()
		raise


def handle_notification_delivery_callback(payload: dict[str, Any]) -> object:
	job_id = _clean(payload.get("job_id"))
	if not job_id:
		raise NotificationDeliveryError("job_id is required")
	if not frappe.db.exists("AOS Notification Delivery Job", job_id):
		raise NotificationDeliveryError("Notification delivery job not found")

	job = frappe.get_doc("AOS Notification Delivery Job", job_id)
	incoming_status = _clean(payload.get("status")).lower()
	canonical_status = {"completed": "delivered", "ready": "delivered"}.get(incoming_status, incoming_status)
	validation = validate_callback_idempotency(job, payload, callback_status=canonical_status)
	if validation.duplicate:
		return job

	terminal_statuses = {"Delivered", "Skipped", "Failed"}
	if job.status in terminal_statuses:
		if job.status == "Delivered" and incoming_status in {"delivered", "completed", "ready"}:
			mark_outbox_callback(
				job_doctype="AOS Notification Delivery Job",
				job_name=job.name,
				callback_status="delivered",
				success=True,
			)
			return job
		if job.status == "Skipped" and incoming_status == "skipped":
			mark_outbox_callback(
				job_doctype="AOS Notification Delivery Job",
				job_name=job.name,
				callback_status="skipped",
				success=True,
			)
			return job
		if job.status == "Failed" and incoming_status == "failed":
			mark_outbox_callback(
				job_doctype="AOS Notification Delivery Job",
				job_name=job.name,
				callback_status="failed",
				success=False,
				error=_clean(payload.get("error")) or "Notification delivery failed",
			)
			return job
		raise NotificationDeliveryError(f"Notification delivery job is already {job.status}")

	job.response_payload = json.dumps(payload, ensure_ascii=False, default=str)
	job.callback_received_at = now_datetime()
	job.success_count = int(payload.get("success_count") or 0)
	job.failure_count = int(payload.get("failure_count") or 0)
	job.token_count = int(payload.get("token_count") or job.token_count or 0)

	inactive_hashes = payload.get("inactive_token_hashes") or []
	if not isinstance(inactive_hashes, list):
		inactive_hashes = []
	inactive_hashes = [_clean(value) for value in inactive_hashes if _clean(value)]
	job.inactive_count = len(inactive_hashes)
	job.inactive_token_hashes = json.dumps(inactive_hashes, ensure_ascii=False)

	_deactivate_inactive_tokens(inactive_hashes)

	if incoming_status in {"delivered", "completed", "ready"}:
		job.status = "Delivered"
		job.completed_at = now_datetime()
		job.last_error = None
		job.save(ignore_permissions=True)
		mark_outbox_callback(
			job_doctype="AOS Notification Delivery Job",
			job_name=job.name,
			callback_status="delivered",
			success=True,
		)
		return job

	if incoming_status == "skipped":
		job.status = "Skipped"
		job.completed_at = now_datetime()
		job.last_error = _clean(payload.get("message")) or None
		job.save(ignore_permissions=True)
		mark_outbox_callback(
			job_doctype="AOS Notification Delivery Job",
			job_name=job.name,
			callback_status="skipped",
			success=True,
		)
		return job

	if incoming_status == "failed":
		job.save(ignore_permissions=True)
		return mark_notification_delivery_job_failed(
			job.name, _clean(payload.get("error")) or "Notification delivery failed", commit=False
		)

	raise NotificationDeliveryError("Invalid notification delivery callback status")


def _deactivate_inactive_tokens(token_hashes: list[str]) -> None:
	for token_hash in token_hashes:
		if not token_hash:
			continue
		frappe.db.set_value(
			"AOS Push Token",
			{"token_hash": token_hash},
			"is_active",
			0,
			update_modified=False,
		)


def mark_notification_delivery_job_failed(
	job_id: str, error: str, *, commit: bool = True, dispatch_failure: bool = False
) -> object:
	job = frappe.get_doc("AOS Notification Delivery Job", job_id)
	job.status = "Failed"
	job.completed_at = now_datetime()
	job.last_error = str(error or "Notification delivery failed")[:1000]
	job.save(ignore_permissions=True)
	if not dispatch_failure:
		mark_outbox_callback(
			job_doctype="AOS Notification Delivery Job",
			job_name=job.name,
			callback_status="failed",
			success=False,
			error=job.last_error,
		)
	if commit:
		frappe.db.commit()
	return job


def retry_queued_notification_delivery_jobs(limit: int = 100) -> dict[str, int]:
	config = get_notification_delivery_config()
	if not config.enabled:
		return {"queued": 0, "failed": 0, "skipped": 0}

	rows = frappe.get_all(
		"AOS Notification Delivery Job",
		filters={
			"status": ["in", ["Queued", "Failed"]],
			"attempt_count": ["<", config.max_attempts],
		},
		pluck="name",
		order_by="creation asc",
		limit=limit,
	)

	queued = 0
	failed = 0
	for name in rows:
		try:
			enqueue_notification_delivery_dispatch(name)
			queued += 1
		except Exception:
			failed += 1
			frappe.log_error(frappe.get_traceback(), f"Failed to enqueue notification delivery retry {name}")

	return {"queued": queued, "failed": failed, "skipped": 0}
