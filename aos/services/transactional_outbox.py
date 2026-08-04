"""Transactional outbox for durable external-service dispatch.

Domain/job rows and outbox rows are inserted in the caller's transaction. This
module never commits from create/ensure helpers. The scheduled publisher is the
recovery source of truth; an after-commit enqueue is only a latency optimization.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import frappe
import requests
from frappe.exceptions import DoesNotExistError, TimestampMismatchError
from frappe.utils import add_to_date, now_datetime

from aos.services.callback_correlation import accepted_dispatch_generations

OUTBOX_DOCTYPE = "AOS Transactional Outbox"
ACTIVE_STATUSES = ("Queued", "Claimed", "Dispatched", "Published", "Dispatch Uncertain", "Reconciliation Pending", "Failed")
CALLBACK_TIMEOUT_ERROR = "DOWNSTREAM_CALLBACK_DEADLINE_EXCEEDED"
DISPATCH_UNCERTAIN_ERROR = "DOWNSTREAM_TRANSPORT_OUTCOME_UNKNOWN"
FINAL_CALLBACK_STATUSES = ("Completed", "Completed With Failure", "Dead Letter", "Cancelled")
NONCLAIMABLE_STATUSES = (*FINAL_CALLBACK_STATUSES, "Manual Review")
TERMINAL_STATUSES = NONCLAIMABLE_STATUSES
SERVICE_TYPES = (
	"video_processing",
	"moderation",
	"search_indexing",
	"notification_delivery",
	"analytics_ingestion",
)


class OutboxError(RuntimeError):
	pass


class OutboxConflictError(OutboxError):
	def __init__(
		self,
		message: str,
		*,
		error_code: str = "OUTBOX_CONFLICT",
		outbox_name: str | None = None,
		counter_field: str | None = None,
	) -> None:
		super().__init__(message)
		self.error_code = error_code
		self.outbox_name = outbox_name
		self.counter_field = counter_field


@dataclass(frozen=True)
class CallbackValidationResult:
	duplicate: bool
	outbox_name: str | None


@dataclass
class ActiveOutboxDispatchContext:
	outbox_name: str
	job_doctype: str
	job_name: str
	stable_dispatch_id: str
	dispatch_generation: int
	dispatch_token: str
	active_dispatch_generation: int
	active_dispatch_token: str
	previous_attempt_count: int
	recovery_dispatch: bool
	dispatch_reason: str
	accepted: bool = False
	outcome: str = ""
	authoritative_generation: int = 0
	work_state: str = ""
	callback_state: str = ""
	terminal_result_type: str = ""
	result_digest: str = ""


_ACTIVE_DISPATCH_CONTEXT: ContextVar[ActiveOutboxDispatchContext | None] = ContextVar(
	"aos_active_outbox_dispatch_context", default=None
)

_ACTIVE_CALLBACK_CORRELATION: ContextVar[dict[str, Any] | None] = ContextVar(
	"aos_active_callback_correlation", default=None
)
_ACCEPTED_DISPATCH_OUTCOMES = {
	"enqueued",
	"stale_generation_replaced",
	"duplicate_active",
	"callback_replay_scheduled",
	"callback_already_completed",
	"work_complete_callback_pending",
	"reconciliation_pending",
	"newer_generation_exists",
}


def current_outbox_dispatch_context(*, job_doctype: str, job_name: str) -> ActiveOutboxDispatchContext | None:
	context = _ACTIVE_DISPATCH_CONTEXT.get()
	if context and context.job_doctype == job_doctype and context.job_name == job_name:
		return context
	return None


@contextmanager
def _activate_dispatch_context(context: ActiveOutboxDispatchContext):
	token = _ACTIVE_DISPATCH_CONTEXT.set(context)
	try:
		yield context
	finally:
		_ACTIVE_DISPATCH_CONTEXT.reset(token)


def record_companion_dispatch_outcome(action: str, details: dict[str, Any] | None = None) -> str:
	"""Record a bounded outcome returned by a signed companion HTTP endpoint."""
	context = _ACTIVE_DISPATCH_CONTEXT.get()
	outcome = _clean(action, limit=80).lower()
	if outcome not in _ACCEPTED_DISPATCH_OUTCOMES:
		raise OutboxError("Companion service returned an unsupported dispatch outcome.")
	if context is None:
		if outcome == "newer_generation_exists":
			raise OutboxConflictError(
				"Companion service already has a newer dispatch generation.",
				error_code="NEWER_DISPATCH_GENERATION",
			)
		return outcome

	data = details if isinstance(details, dict) else {}
	context.accepted = True
	context.outcome = outcome
	try:
		context.authoritative_generation = max(0, int(data.get("authoritative_generation") or 0))
	except (TypeError, ValueError):
		context.authoritative_generation = 0
	context.work_state = _clean(data.get("work_state"), limit=80).lower()
	context.callback_state = _clean(data.get("callback_state"), limit=80).lower()
	context.terminal_result_type = _clean(data.get("terminal_result_type"), limit=80).lower()
	context.result_digest = _clean(data.get("result_digest"), limit=128).lower()
	if context.recovery_dispatch:
		_record_operational_event(_get_spec_for_doctype(context.job_doctype).service_type, "redispatch_accepted")
	if outcome == "duplicate_active":
		_record_operational_event(_get_spec_for_doctype(context.job_doctype).service_type, "duplicate_active_dispatch")
	elif outcome in {"callback_replay_scheduled", "work_complete_callback_pending"}:
		_record_operational_event(_get_spec_for_doctype(context.job_doctype).service_type, "callback_replay")
	return outcome


@dataclass(frozen=True)
class OutboxDispatchSpec:
	service_type: str
	job_doctype: str
	dispatch_method: str
	kwarg_name: str


DISPATCH_SPECS: dict[str, OutboxDispatchSpec] = {
	"video_processing": OutboxDispatchSpec(
		"video_processing",
		"AOS Video Processing Job",
		"aos.tasks.video_processing.dispatch_video_processing_job",
		"video_job_id",
	),
	"moderation": OutboxDispatchSpec(
		"moderation",
		"AOS Moderation Job",
		"aos.tasks.moderation.dispatch_moderation_job",
		"moderation_job_id",
	),
	"search_indexing": OutboxDispatchSpec(
		"search_indexing",
		"AOS Search Index Job",
		"aos.tasks.search_ranking.dispatch_search_index_job",
		"search_job_id",
	),
	"notification_delivery": OutboxDispatchSpec(
		"notification_delivery",
		"AOS Notification Delivery Job",
		"aos.tasks.notification_delivery.dispatch_notification_delivery_job",
		"delivery_job_id",
	),
	"analytics_ingestion": OutboxDispatchSpec(
		"analytics_ingestion",
		"AOS Analytics Ingest Job",
		"aos.tasks.analytics_pipeline.dispatch_analytics_ingest_job",
		"analytics_job_id",
	),
}


def _clean(value: Any, *, limit: int = 1000) -> str:
	return str(value or "").strip()[:limit]


def _json_dumps(value: Any) -> str:
	return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _record_operational_event(service_type: str, event: str) -> None:
	try:
		from aos.utils.metrics import record_outbox_event

		record_outbox_event(service_type, event)
	except Exception:
		pass


def stable_idempotency_key(service_type: str, *parts: Any) -> str:
	service = _clean(service_type, limit=80).lower()
	if service not in SERVICE_TYPES:
		raise OutboxError("Unsupported outbox service type.")
	canonical = "\x1f".join(_clean(part, limit=500) for part in parts)
	digest = hashlib.sha256(f"{service}\x1e{canonical}".encode()).hexdigest()
	return f"aos:{service}:{digest}"


def downstream_idempotency_key(job: Any, service_type: str) -> str:
	stored = _clean(getattr(job, "idempotency_key", None), limit=200)
	if stored:
		return stored
	return stable_idempotency_key(service_type, getattr(job, "doctype", ""), getattr(job, "name", ""))


def _get_spec(service_type: str) -> OutboxDispatchSpec:
	try:
		return DISPATCH_SPECS[_clean(service_type, limit=80).lower()]
	except KeyError as exc:
		raise OutboxError("Unsupported outbox service type.") from exc


def _get_spec_for_doctype(job_doctype: str) -> OutboxDispatchSpec:
	for spec in DISPATCH_SPECS.values():
		if spec.job_doctype == job_doctype:
			return spec
	raise OutboxError("Unsupported outbox job DocType.")


_CALLBACK_TIMEOUT_ENV = {
	"video_processing": ("AOS_VIDEO_CALLBACK_TIMEOUT_SECONDS", 2700),
	"moderation": ("AOS_MODERATION_CALLBACK_TIMEOUT_SECONDS", 900),
	"search_indexing": ("AOS_SEARCH_CALLBACK_TIMEOUT_SECONDS", 900),
	"notification_delivery": ("AOS_NOTIFICATION_CALLBACK_TIMEOUT_SECONDS", 900),
	"analytics_ingestion": ("AOS_ANALYTICS_CALLBACK_TIMEOUT_SECONDS", 900),
}


def callback_timeout_seconds(service_type: str) -> int:
	env_name, default = _CALLBACK_TIMEOUT_ENV[_get_spec(service_type).service_type]
	try:
		value = int(os.getenv(env_name, str(default)) or default)
	except (TypeError, ValueError):
		value = default
	return max(120, min(value, 86400))


def is_ambiguous_dispatch_error(exc: Exception) -> bool:
	name = exc.__class__.__name__.lower()
	return any(token in name for token in ("timeout", "connection", "connect", "reset", "proxy", "chunked", "closed"))


def _outbox_aggregate_exists(outbox: Any) -> bool:
	aggregate_doctype = _clean(getattr(outbox, "aggregate_doctype", None), limit=140)
	aggregate_name = _clean(getattr(outbox, "aggregate_name", None), limit=140)
	if not aggregate_doctype or not aggregate_name:
		return True
	return bool(frappe.db.exists(aggregate_doctype, aggregate_name))


def _save_outbox(outbox: Any) -> None:
	"""Persist outbox lifecycle state after its optional aggregate is deleted.

	The durable job link remains authoritative. The aggregate Dynamic Link is
	audit metadata and is allowed to become stale after an aggregate deletion.
	"""
	if not _outbox_aggregate_exists(outbox):
		outbox.flags.ignore_links = True
	outbox.save(ignore_permissions=True)


def ensure_outbox_for_job(
	*,
	service_type: str,
	job: Any,
	queue: str,
	timeout_seconds: int,
	aggregate_doctype: str | None = None,
	aggregate_name: str | None = None,
	max_attempts: int | None = None,
) -> Any:
	"""Persist one outbox row in the caller's current transaction.

	No commit occurs here. A caller rollback removes both the domain/job change
	and this row. Existing rows are returned by stable idempotency key.
	"""

	spec = _get_spec(service_type)
	job_doctype = _clean(getattr(job, "doctype", None), limit=140)
	job_name = _clean(getattr(job, "name", None), limit=140)
	if job_doctype != spec.job_doctype or not job_name:
		raise OutboxError("Outbox job type does not match the service dispatch specification.")

	existing_for_job = frappe.db.get_value(
		OUTBOX_DOCTYPE,
		{"job_doctype": job_doctype, "job_name": job_name},
		"name",
	)
	if existing_for_job:
		row = frappe.get_doc(OUTBOX_DOCTYPE, existing_for_job)
		if _clean(row.service_type, limit=80) != spec.service_type:
			raise OutboxConflictError("Durable job is already correlated with another outbox service type.")
		return row

	job_key = downstream_idempotency_key(job, spec.service_type)
	outbox_key = stable_idempotency_key(spec.service_type, job_key)
	existing = frappe.db.get_value(OUTBOX_DOCTYPE, {"idempotency_key": outbox_key}, "name")
	if existing:
		row = frappe.get_doc(OUTBOX_DOCTYPE, existing)
		if row.job_doctype != job_doctype or row.job_name != job_name:
			raise OutboxConflictError("Outbox idempotency key is already correlated with another job.")
		return row

	kwargs = {spec.kwarg_name: job_name}
	outbox = frappe.get_doc(
		{
			"doctype": OUTBOX_DOCTYPE,
			"service_type": spec.service_type,
			"status": "Queued",
			"aggregate_doctype": aggregate_doctype,
			"aggregate_name": aggregate_name,
			"job_doctype": job_doctype,
			"job_name": job_name,
			"dispatch_method": spec.dispatch_method,
			"dispatch_kwargs_json": _json_dumps(kwargs),
			"queue": _clean(queue, limit=80) or "default",
			"timeout_seconds": max(30, min(int(timeout_seconds or 300), 7200)),
			"callback_timeout_seconds": callback_timeout_seconds(spec.service_type),
			"rq_job_id": f"outbox:{outbox_key}",
			"idempotency_key": outbox_key,
			"attempt_count": 0,
			"max_attempts": max(1, min(int(max_attempts or getattr(job, "max_attempts", 5) or 5), 25)),
			"next_attempt_at": now_datetime(),
			"created_at": now_datetime(),
		}
	)
	if not _outbox_aggregate_exists(outbox):
		outbox.flags.ignore_links = True
	try:
		outbox.insert(ignore_permissions=True)
	except Exception:
		# A concurrent transaction may have inserted the same unique
		# idempotency key after our initial read. Return that exact row when
		# correlation agrees; otherwise preserve the original error.
		concurrent_name = frappe.db.get_value(OUTBOX_DOCTYPE, {"idempotency_key": outbox_key}, "name")
		if not concurrent_name:
			raise
		concurrent = frappe.get_doc(OUTBOX_DOCTYPE, concurrent_name)
		if concurrent.job_doctype != job_doctype or concurrent.job_name != job_name:
			raise OutboxConflictError("Outbox idempotency key is already correlated with another job.")
		return concurrent
	register_after_commit_publish()
	return outbox


def register_after_commit_publish() -> None:
	"""Best-effort low-latency publish after commit.

	The durable scheduler remains authoritative. A crash before this callback or
	before RQ enqueue leaves a committed Queued row for scheduled recovery.
	"""

	try:
		flags = getattr(frappe.local, "flags", None)
		if flags is not None and getattr(flags, "aos_outbox_after_commit_registered", False):
			return
		if flags is not None:
			flags.aos_outbox_after_commit_registered = True

		def _enqueue_publisher() -> None:
			try:
				frappe.enqueue(
					"aos.tasks.outbox.publish_transactional_outbox",
					queue="short",
					timeout=300,
					enqueue_after_commit=False,
					job_id="aos-transactional-outbox-publisher",
				)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(), "Transactional outbox after-commit publish enqueue failed"
				)

		after_commit = getattr(frappe.db, "after_commit", None)
		if after_commit is not None and hasattr(after_commit, "add"):
			after_commit.add(_enqueue_publisher)
	except Exception:
		# Registration failure must not break the domain transaction. The
		# persisted row is recovered by the recurring publisher.
		frappe.log_error(frappe.get_traceback(), "Transactional outbox after-commit registration failed")


def retry_delay_seconds(attempt_count: int, *, base_seconds: int = 15, max_seconds: int = 3600) -> int:
	attempt = max(1, int(attempt_count or 1))
	return min(max_seconds, base_seconds * (2 ** min(attempt - 1, 10)))


def sanitized_dispatch_error(exc: Exception) -> str:
	"""Return a stable non-sensitive category for durable job state."""

	name = exc.__class__.__name__.lower()
	if "timeout" in name:
		return "DOWNSTREAM_DISPATCH_TIMEOUT"
	if any(token in name for token in ("connection", "connect", "dns", "proxy")):
		return "DOWNSTREAM_CONNECTION_FAILED"
	if any(token in name for token in ("http", "status")):
		return "DOWNSTREAM_HTTP_REJECTED"
	if any(token in name for token in ("json", "decode", "value", "type")):
		return "DOWNSTREAM_RESPONSE_INVALID"
	return "DOWNSTREAM_DISPATCH_FAILED"


def _publisher_owner() -> str:
	return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


def recover_stale_claims(*, now=None, outbox_name: str | None = None) -> int:
	current = now or now_datetime()
	name_filter = " AND name = %s" if outbox_name else ""
	params: tuple[Any, ...] = (
		(current, current, _clean(outbox_name, limit=140))
		if outbox_name
		else (current, current)
	)
	frappe.db.sql(
		f"""
	    UPDATE `tab{OUTBOX_DOCTYPE}`
	    SET status = CASE WHEN status IN ('Claimed', 'Dispatched') THEN 'Queued' ELSE status END,
	        claimed_by = NULL, claim_token = NULL,
	        claimed_at = NULL, lease_expires_at = NULL, next_attempt_at = %s,
	        last_error = 'Publisher claim lease expired before completion',
	        pending_dispatch_reason = 'publisher_lease_expired'
	    WHERE lease_expires_at IS NOT NULL
	      AND lease_expires_at < %s{name_filter}
	    """,
		params,
	)
	# Frappe's sql return value differs by driver; rowcount is available on the cursor.
	try:
		return int(frappe.db._cursor.rowcount or 0)
	except Exception:
		return 0


def _companion_status_config(service_type: str) -> tuple[str, str, str]:
	if service_type == "video_processing":
		from aos.services.video_processing_service import get_video_processing_config

		config = get_video_processing_config()
		return config.service_url, config.service_secret, "X-AOS-Signature"
	if service_type == "moderation":
		from aos.services.moderation_service import get_moderation_config

		config = get_moderation_config()
		return config.service_url, config.service_secret, "X-AOS-Moderation-Signature"
	if service_type == "search_indexing":
		from aos.services.search_ranking_service import get_search_ranking_config

		config = get_search_ranking_config()
		return config.service_url, config.service_secret, "X-AOS-Search-Signature"
	if service_type == "notification_delivery":
		from aos.services.notification_delivery_service import get_notification_delivery_config

		config = get_notification_delivery_config()
		return config.service_url, config.service_secret, "X-AOS-Notification-Signature"
	if service_type == "analytics_ingestion":
		from aos.services.analytics_pipeline_service import get_analytics_pipeline_config

		config = get_analytics_pipeline_config()
		return config.service_url, config.service_secret, "X-AOS-Analytics-Signature"
	raise OutboxError("Unsupported companion status service type.")


def query_companion_job_status(outbox: Any) -> dict[str, Any]:
	"""Query one private signed companion status endpoint using stable identity."""
	from aos.api.shared.callback_security import build_signature

	job = frappe.get_doc(outbox.job_doctype, outbox.job_name)
	stable_job_id = downstream_idempotency_key(job, outbox.service_type)
	service_url, secret, header_name = _companion_status_config(outbox.service_type)
	body = _json_dumps({"job_id": outbox.job_name, "idempotency_key": stable_job_id}).encode("utf-8")
	response = requests.post(
		f"{service_url.rstrip('/')}/internal/jobs/status",
		data=body,
		headers={"Content-Type": "application/json", header_name: build_signature(secret, body)},
		timeout=10,
	)
	response.raise_for_status()
	data = response.json() if response.content else {}
	if not isinstance(data, dict):
		raise OutboxError("Companion status response is invalid.")
	state = _clean(data.get("state"), limit=80).lower()
	if state not in {"queued", "started", "callback_pending", "callback_complete", "failed", "absent", "unknown"}:
		raise OutboxError("Companion status response has an unsupported state.")
	return {
		"state": state,
		"work_state": _clean(data.get("work_state"), limit=80).lower(),
		"callback_state": _clean(data.get("callback_state"), limit=80).lower(),
		"dispatch_generation": max(0, int(data.get("dispatch_generation") or 0)),
		"heartbeat_at": _clean(data.get("heartbeat_at"), limit=80),
		"phase": _clean(data.get("phase"), limit=80),
		"terminal_result_type": _clean(data.get("terminal_result_type"), limit=80).lower(),
		"result_digest": _clean(data.get("result_digest"), limit=128).lower(),
		"work_error_classification": _clean(data.get("work_error_classification"), limit=80).lower(),
	}


def _reconciliation_max(outbox: Any) -> int:
	return max(1, int(getattr(outbox, "reconciliation_max_attempts", 0) or 10))


def _schedule_reconciliation(
	outbox: Any,
	*,
	now: Any,
	outcome: str,
	error: str,
	companion_generation: int = 0,
) -> str:
	"""Schedule bounded reconciliation without consuming work-dispatch attempts forever."""
	count = int(getattr(outbox, "reconciliation_attempt_count", 0) or 0) + 1
	outbox.reconciliation_attempt_count = count
	outbox.last_reconciliation_outcome = _clean(outcome, limit=120)
	if companion_generation > 0:
		outbox.companion_authoritative_generation = max(
			int(getattr(outbox, "companion_authoritative_generation", 0) or 0),
			int(companion_generation),
		)
	outbox.last_error = _clean(error, limit=180)
	if count >= _reconciliation_max(outbox):
		outbox.status = "Manual Review"
		outbox.manual_review_reason = _clean(error, limit=180)
		outbox.completed_at = now
		outbox.next_attempt_at = None
		_record_operational_event(outbox.service_type, "reconciliation_exhausted")
		return outbox.status
	outbox.status = "Reconciliation Pending"
	outbox.next_attempt_at = add_to_date(
		now,
		seconds=retry_delay_seconds(count, base_seconds=30, max_seconds=21600),
		as_datetime=True,
	)
	return outbox.status


_SUCCESS_JOB_STATUSES: dict[str, set[str]] = {
	"AOS Video Processing Job": {"Ready"},
	"AOS Moderation Job": {"Allowed", "Review Required", "Rejected"},
	"AOS Search Index Job": {"Indexed", "Deleted"},
	"AOS Notification Delivery Job": {"Delivered", "Skipped"},
	"AOS Analytics Ingest Job": {"Ingested", "Skipped"},
}


def _repair_callback_completed_outbox(outbox: Any, status_data: dict[str, Any], *, now: Any) -> str:
	"""Repair only when durable Frappe state independently proves callback completion."""
	job = frappe.get_doc(outbox.job_doctype, outbox.job_name)
	job_status = _clean(getattr(job, "status", None), limit=80)
	work_state = _clean(status_data.get("work_state"), limit=80).lower()
	terminal_type = _clean(status_data.get("terminal_result_type"), limit=80).lower()
	result_digest = _clean(status_data.get("result_digest"), limit=128).lower()
	companion_generation = max(0, int(status_data.get("dispatch_generation") or 0))
	if not result_digest or work_state not in {"work_complete", "work_failed"}:
		return _schedule_reconciliation(
			outbox,
			now=now,
			outcome="callback_complete_insufficient_evidence",
			error="COMPANION_CALLBACK_COMPLETE_EVIDENCE_INSUFFICIENT",
			companion_generation=companion_generation,
		)
	if work_state == "work_failed" and job_status == "Failed":
		outbox.status = "Completed With Failure"
		outbox.callback_status = terminal_type or "failed"
		outbox.last_error = _clean(getattr(job, "last_error", None), limit=180) or "DOWNSTREAM_WORK_FAILED"
	elif job_status in _SUCCESS_JOB_STATUSES.get(outbox.job_doctype, set()):
		outbox.status = "Completed"
		outbox.callback_status = terminal_type or job_status.lower()
		outbox.last_error = None
	else:
		return _schedule_reconciliation(
			outbox,
			now=now,
			outcome="callback_complete_state_mismatch",
			error="COMPANION_CALLBACK_COMPLETE_FRAPPE_STATE_MISMATCH",
			companion_generation=companion_generation,
		)
	outbox.completed_at = now
	outbox.callback_received_at = getattr(job, "callback_received_at", None) or now
	outbox.last_callback_at = outbox.callback_received_at
	outbox.completed_dispatch_generation = companion_generation or int(outbox.dispatch_generation or 0)
	outbox.companion_authoritative_generation = max(
		int(getattr(outbox, "companion_authoritative_generation", 0) or 0), companion_generation
	)
	outbox.terminal_result_type = terminal_type
	outbox.terminal_result_digest = result_digest
	outbox.next_attempt_at = None
	outbox.callback_deadline_at = None
	_clear_claim(outbox)
	_clear_proposal(outbox)
	_record_operational_event(outbox.service_type, "automatic_outbox_repair")
	return outbox.status


def recover_overdue_published(
	*, now=None, limit: int = 1000, outbox_name: str | None = None
) -> dict[str, int]:
	"""Reconcile overdue callbacks while preserving callback eligibility and bounded convergence."""
	current = now or now_datetime()
	limit = max(1, min(int(limit or 1000), 5000))
	requeued = 0
	dead_lettered = 0
	extended_active = 0
	pending_reconciliation = 0
	manual_review = 0
	concurrent_updates_skipped = 0
	filters: dict[str, Any] = {"status": "Published", "callback_deadline_at": ("<", current)}
	if outbox_name:
		filters["name"] = _clean(outbox_name, limit=140)
	rows = frappe.get_all(
		OUTBOX_DOCTYPE,
		filters=filters,
		fields=["name"],
		order_by="callback_deadline_at asc, name asc",
		limit=limit,
	)
	for row in rows:
		outbox = frappe.get_doc(OUTBOX_DOCTYPE, row.name)
		try:
			status_data = query_companion_job_status(outbox)
			state = status_data["state"]
			companion_generation = max(0, int(status_data.get("dispatch_generation") or 0))
			outbox.last_reconciliation_at = current
			outbox.companion_work_state = status_data.get("work_state") or state
			outbox.companion_callback_state = status_data.get("callback_state")
			outbox.last_worker_heartbeat_at = status_data.get("heartbeat_at") or None
			outbox.companion_authoritative_generation = max(
				int(getattr(outbox, "companion_authoritative_generation", 0) or 0), companion_generation
			)
			if state in {"queued", "started"}:
				outbox.callback_deadline_at = add_to_date(
					current, seconds=max(120, int(outbox.callback_timeout_seconds or 900)), as_datetime=True
				)
				outbox.duplicate_active_observation_count = int(outbox.duplicate_active_observation_count or 0) + 1
				outbox.last_error = None
				outbox.last_reconciliation_outcome = "active_observed"
				extended_active += 1
			elif state == "callback_pending":
				outbox.callback_timeout_count = int(outbox.callback_timeout_count or 0) + 1
				outbox.callback_deadline_at = None
				outbox.pending_dispatch_reason = "callback_reconciliation"
				status = _schedule_reconciliation(
					outbox,
					now=current,
					outcome="callback_pending",
					error=CALLBACK_TIMEOUT_ERROR,
					companion_generation=companion_generation,
				)
				if status == "Manual Review":
					manual_review += 1
				else:
					requeued += 1
			elif state == "callback_complete" or (
				state == "failed" and status_data.get("callback_state") == "complete"
			):
				status = _repair_callback_completed_outbox(outbox, status_data, now=current)
				if status == "Manual Review":
					manual_review += 1
				elif status == "Reconciliation Pending":
					pending_reconciliation += 1
			elif state in {"failed", "absent", "unknown"}:
				outbox.callback_deadline_at = None
				outbox.pending_dispatch_reason = "work_absent_recovery" if state == "absent" else "status_reconciliation"
				status = _schedule_reconciliation(
					outbox,
					now=current,
					outcome=state,
					error="COMPANION_WORK_ABSENT" if state == "absent" else "COMPANION_STATUS_UNRESOLVED",
					companion_generation=companion_generation,
				)
				if status == "Manual Review":
					manual_review += 1
				else:
					pending_reconciliation += 1
			_save_outbox(outbox)
		except (DoesNotExistError, TimestampMismatchError):
			# Another publisher or callback updated or removed this row after it was read.
			# Treat that concurrent writer as authoritative and retry naturally on
			# the next scheduled recovery pass if the row is still overdue.
			concurrent_updates_skipped += 1
			continue
		except Exception as exc:
			# Reload before recording a status-query failure so a stale document
			# cannot overwrite a concurrent callback or publisher transition.
			try:
				outbox.reload()
				if outbox.status != "Published" or not outbox.callback_deadline_at or outbox.callback_deadline_at >= current:
					concurrent_updates_skipped += 1
					continue
				status = _schedule_reconciliation(
					outbox,
					now=current,
					outcome="status_query_failed",
					error=sanitized_dispatch_error(exc),
				)
				_save_outbox(outbox)
			except (DoesNotExistError, TimestampMismatchError):
				concurrent_updates_skipped += 1
				continue
			if status == "Manual Review":
				manual_review += 1
			else:
				pending_reconciliation += 1
	return {
		"requeued": requeued,
		"dead_lettered": dead_lettered,
		"extended_active": extended_active,
		"pending_reconciliation": pending_reconciliation,
		"manual_review": manual_review,
		"concurrent_updates_skipped": concurrent_updates_skipped,
	}

def outbox_dispatch_context(*, job_doctype: str, job_name: str) -> dict[str, Any]:
	"""Return callback correlation for the currently leased private dispatch."""

	context = current_outbox_dispatch_context(job_doctype=job_doctype, job_name=job_name)
	if context is None:
		row = frappe.db.get_value(
			OUTBOX_DOCTYPE,
			{"job_doctype": job_doctype, "job_name": job_name},
			["idempotency_key", "dispatch_generation", "current_dispatch_token"],
			as_dict=True,
		)
		if not row:
			return {}
		return {
			"dispatch_id": _clean(row.get("idempotency_key"), limit=200),
			"dispatch_generation": int(row.get("dispatch_generation") or 0),
			"dispatch_token": _clean(row.get("current_dispatch_token"), limit=140),
		}
	return {
		"dispatch_id": context.stable_dispatch_id,
		"dispatch_generation": context.dispatch_generation,
		"dispatch_token": context.dispatch_token,
	}


def _claim_one(
	*, owner: str, lease_seconds: int, now=None, outbox_name: str | None = None
) -> dict[str, Any] | None:
	current = now or now_datetime()
	name_filter = " AND name = %s" if outbox_name else ""
	query_params: tuple[Any, ...] = (
		(current, _clean(outbox_name, limit=140), current) if outbox_name else (current, current)
	)
	rows = frappe.db.sql(
		f"""
		    SELECT name, status, attempt_count, max_attempts, pending_dispatch_reason,
	           dispatch_generation, current_dispatch_token,
		           proposed_dispatch_generation, proposed_dispatch_token,
		           companion_authoritative_generation
	    FROM `tab{OUTBOX_DOCTYPE}`
	    WHERE status IN ('Queued', 'Failed', 'Dispatch Uncertain', 'Reconciliation Pending')
	      AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
	      AND attempt_count < max_attempts{name_filter}
		      AND (claim_token IS NULL OR lease_expires_at IS NULL OR lease_expires_at < %s)
	    ORDER BY creation ASC, name ASC
	    LIMIT 1
	    FOR UPDATE SKIP LOCKED
	    """,
		query_params,
		as_dict=True,
	)
	if not rows:
		return None
	row = rows[0]
	previous_attempt = int(row.get("attempt_count") or 0)
	reason = _clean(row.get("pending_dispatch_reason"), limit=80) or "initial"
	if reason == "initial" and previous_attempt > 0:
		reason = "dispatch_retry"
	claim_token = uuid.uuid4().hex
	attempt = previous_attempt + 1
	active_generation = max(0, int(row.get("dispatch_generation") or 0))
	existing_proposed_generation = max(0, int(row.get("proposed_dispatch_generation") or 0))
	existing_proposed_token = _clean(row.get("proposed_dispatch_token"), limit=140)
	reuse_uncertain_proposal = (
		_clean(row.get("status"), limit=80) in {"Dispatch Uncertain", "Reconciliation Pending"}
		and existing_proposed_generation > 0
		and bool(existing_proposed_token)
	)
	if reuse_uncertain_proposal:
		# A timed-out HTTP request may already have been accepted. Retry the
		# exact same proposed correlation until the companion reports a bounded
		# authoritative outcome; never invalidate a matching late callback.
		proposed_generation = existing_proposed_generation
		proposed_token = existing_proposed_token
	else:
		companion_floor = max(0, int(row.get("companion_authoritative_generation") or 0))
		proposed_generation = max(1, active_generation + 1, companion_floor + 1)
		proposed_token = uuid.uuid4().hex
	lease_expires = add_to_date(current, seconds=max(30, min(int(lease_seconds), 3600)), as_datetime=True)
	frappe.db.sql(
		f"""
	    UPDATE `tab{OUTBOX_DOCTYPE}`
		    SET claimed_by = %s, claim_token = %s,
	        claimed_at = %s, lease_expires_at = %s, attempt_count = %s,
	        proposed_dispatch_generation = %s, proposed_dispatch_token = %s,
	        active_dispatch_reason = %s, pending_dispatch_reason = 'initial',
	        callback_deadline_at = NULL, last_error = NULL
	    WHERE name = %s
		      AND status IN ('Queued', 'Failed', 'Dispatch Uncertain', 'Reconciliation Pending')
		      AND (claim_token IS NULL OR lease_expires_at IS NULL OR lease_expires_at < %s)
	    """,
		(owner, claim_token, current, lease_expires, attempt, proposed_generation, proposed_token, reason, row.name, current),
	)
	return {
		"name": row.name,
		"claim_token": claim_token,
		"attempt_count": attempt,
		"previous_attempt_count": previous_attempt,
		"dispatch_generation": proposed_generation,
		"dispatch_token": proposed_token,
		"active_dispatch_generation": active_generation,
		"active_dispatch_token": _clean(row.get("current_dispatch_token"), limit=140),
		"dispatch_reason": reason,
		"lifecycle_status": row.get("status"),
	}


def _mark_enqueue_accepted(name: str, token: str) -> None:
	frappe.db.sql(
		f"""
        UPDATE `tab{OUTBOX_DOCTYPE}`
	        SET dispatched_at = %s
	        WHERE name = %s AND claim_token = %s
        """,
		(now_datetime(), name, token),
	)


def _mark_publish_failure(name: str, token: str, error: str) -> str:
	row = (
		frappe.db.get_value(
			OUTBOX_DOCTYPE,
			name,
			["attempt_count", "max_attempts"],
			as_dict=True,
		)
		or {}
	)
	attempt = int(row.get("attempt_count") or 0)
	maximum = int(row.get("max_attempts") or 5)
	terminal = attempt >= maximum
	status = "Dead Letter" if terminal else "Failed"
	next_attempt = (
		None
		if terminal
		else add_to_date(now_datetime(), seconds=retry_delay_seconds(attempt), as_datetime=True)
	)
	frappe.db.sql(
		f"""
        UPDATE `tab{OUTBOX_DOCTYPE}`
        SET status = %s, last_error = %s, next_attempt_at = %s,
            claimed_by = NULL, claim_token = NULL, claimed_at = NULL,
            lease_expires_at = NULL, completed_at = %s,
            pending_dispatch_reason = CASE
              WHEN %s = 'Dead Letter' THEN pending_dispatch_reason
              ELSE COALESCE(NULLIF(active_dispatch_reason, ''), 'dispatch_retry')
            END
        WHERE name = %s AND claim_token = %s
        """,
		(
			status,
			_clean(error) or "Outbox publish failed",
			next_attempt,
			now_datetime() if terminal else None,
			status,
			name,
			token,
		),
	)
	return status


def _mark_dispatch_uncertain(name: str, token: str, error: str) -> str:
	row = frappe.db.get_value(
		OUTBOX_DOCTYPE, name, ["attempt_count", "max_attempts"], as_dict=True
	) or {}
	attempt = int(row.get("attempt_count") or 0)
	maximum = int(row.get("max_attempts") or 5)
	terminal = attempt >= maximum
	status = "Manual Review" if terminal else "Dispatch Uncertain"
	next_attempt = None if terminal else add_to_date(
		now_datetime(), seconds=retry_delay_seconds(attempt), as_datetime=True
	)
	frappe.db.sql(
		f"""
	    UPDATE `tab{OUTBOX_DOCTYPE}`
	    SET status = %s, last_error = %s, next_attempt_at = %s,
	        dispatch_uncertain_count = dispatch_uncertain_count + 1,
	        reconciliation_attempt_count = reconciliation_attempt_count + 1,
	        claimed_by = NULL, claim_token = NULL, claimed_at = NULL,
	        lease_expires_at = NULL, completed_at = %s,
	        pending_dispatch_reason = CASE WHEN %s = 'Manual Review'
	          THEN pending_dispatch_reason ELSE 'dispatch_uncertain' END,
	        manual_review_reason = CASE WHEN %s = 'Manual Review'
	          THEN %s ELSE manual_review_reason END
	    WHERE name = %s AND claim_token = %s
	    """,
		(
			status,
			_clean(error, limit=120) or DISPATCH_UNCERTAIN_ERROR,
			next_attempt,
			now_datetime() if terminal else None,
			status,
			status,
			"Dispatch transport outcome remained unresolved after bounded reconciliation.",
			name,
			token,
		),
	)
	return status


def publish_outbox_records(
	*, limit: int = 100, lease_seconds: int = 300, outbox_name: str | None = None
) -> dict[str, int]:
	"""Claim and enqueue due outbox rows safely across concurrent publishers."""

	limit = max(1, min(int(limit or 100), 1000))
	owner = _publisher_owner()
	recovered = recover_stale_claims(outbox_name=outbox_name)
	callback_recovery = recover_overdue_published(limit=limit, outbox_name=outbox_name)
	frappe.db.commit()
	claimed = 0
	dispatched = 0
	failures = 0
	dead_lettered = 0

	for _ in range(limit):
		claim = _claim_one(
			owner=owner, lease_seconds=lease_seconds, outbox_name=outbox_name
		)
		frappe.db.commit()  # release the row lock before interacting with Redis
		if not claim:
			break
		claimed += 1
		row = frappe.get_doc(OUTBOX_DOCTYPE, claim["name"])
		try:
			frappe.enqueue(
				"aos.tasks.outbox.dispatch_claimed_outbox",
				outbox_name=row.name,
				claim_token=claim["claim_token"],
				queue=row.queue or "default",
				timeout=max(30, int(row.timeout_seconds or 300)),
				enqueue_after_commit=False,
				job_id=f"{row.rq_job_id or f'outbox:{row.idempotency_key}'}:g{int(claim['attempt_count'])}",
			)
			_mark_enqueue_accepted(row.name, claim["claim_token"])
			frappe.db.commit()
			dispatched += 1
		except Exception:
			status = _mark_publish_failure(row.name, claim["claim_token"], "RQ_ENQUEUE_FAILED")
			frappe.db.commit()
			failures += 1
			dead_lettered += int(status == "Dead Letter")
			frappe.log_error(frappe.get_traceback(), f"Transactional outbox enqueue failed: {row.name}")

	return {
		"recovered_stale_claims": recovered,
		"recovered_overdue_callbacks": callback_recovery["requeued"],
		"callback_timeout_dead_lettered": callback_recovery["dead_lettered"],
		"claimed": claimed,
		"dispatched": dispatched,
		"failed": failures,
		"dead_lettered": dead_lettered,
	}


def _update_dispatch_outcome_counters(outbox: Any, context: ActiveOutboxDispatchContext) -> None:
	if context.recovery_dispatch and context.accepted:
		outbox.redispatch_accepted_count = int(outbox.redispatch_accepted_count or 0) + 1
	if context.outcome == "duplicate_active":
		outbox.duplicate_active_dispatch_count = int(outbox.duplicate_active_dispatch_count or 0) + 1
	if context.outcome in {"callback_replay_scheduled", "work_complete_callback_pending"}:
		outbox.callback_replay_count = int(outbox.callback_replay_count or 0) + 1


def _clear_claim(outbox: Any) -> None:
	outbox.claimed_by = None
	outbox.claim_token = None
	outbox.claimed_at = None
	outbox.lease_expires_at = None


def _clear_proposal(outbox: Any) -> None:
	outbox.proposed_dispatch_generation = 0
	outbox.proposed_dispatch_token = None


def _promote_proposal(outbox: Any, context: ActiveOutboxDispatchContext) -> None:
	outbox.dispatch_generation = int(context.dispatch_generation)
	outbox.current_dispatch_token = context.dispatch_token
	_clear_proposal(outbox)


def dispatch_claimed_outbox(outbox_name: str, claim_token: str) -> Any:
	"""Execute one leased dispatch and reconcile companion work/callback state."""
	outbox_name = _clean(outbox_name, limit=140)
	claim_token = _clean(claim_token, limit=140)
	outbox = frappe.get_doc(OUTBOX_DOCTYPE, outbox_name)
	if outbox.status in TERMINAL_STATUSES or outbox.status == "Published":
		return outbox
	if outbox.claim_token != claim_token:
		raise OutboxConflictError("Outbox claim token no longer owns this dispatch.")
	if outbox.status not in {"Queued", "Failed", "Dispatch Uncertain", "Reconciliation Pending", "Claimed", "Dispatched"}:
		raise OutboxConflictError(f"Outbox record cannot dispatch from status {outbox.status}.")

	kwargs = json.loads(outbox.dispatch_kwargs_json or "{}")
	reason = _clean(getattr(outbox, "active_dispatch_reason", None), limit=80) or "initial"
	context = ActiveOutboxDispatchContext(
		outbox_name=outbox.name,
		job_doctype=outbox.job_doctype,
		job_name=outbox.job_name,
		stable_dispatch_id=_clean(outbox.idempotency_key, limit=200),
		dispatch_generation=int(outbox.proposed_dispatch_generation or 0),
		dispatch_token=_clean(outbox.proposed_dispatch_token, limit=140),
		active_dispatch_generation=int(outbox.dispatch_generation or 0),
		active_dispatch_token=_clean(outbox.current_dispatch_token, limit=140),
		previous_attempt_count=max(0, int(outbox.attempt_count or 1) - 1),
		recovery_dispatch=reason != "initial",
		dispatch_reason=reason,
	)
	try:
		with _activate_dispatch_context(context):
			result = frappe.get_attr(outbox.dispatch_method)(**kwargs)
		outbox.reload()
		if outbox.status in TERMINAL_STATUSES:
			return result
		if not context.accepted:
			raise OutboxError("Dispatch returned without an accepted companion-service outcome.")

		now = now_datetime()
		callback_deadline = add_to_date(
			now, seconds=max(120, min(int(outbox.callback_timeout_seconds or 900), 86400)), as_datetime=True
		)
		outcome = context.outcome
		if outcome in {"enqueued", "stale_generation_replaced", "callback_replay_scheduled", "work_complete_callback_pending"}:
			_promote_proposal(outbox, context)
			outbox.status = "Published"
			outbox.published_at = now
			outbox.callback_deadline_at = callback_deadline
		elif outcome == "duplicate_active":
			# A running generation remains authoritative. Only promote a proposed
			# generation when the companion confirms that exact generation.
			confirmed_proposed_generation = (
				context.active_dispatch_generation <= 0
				and context.dispatch_generation > 0
				and context.authoritative_generation == context.dispatch_generation
			)
			if confirmed_proposed_generation:
				_promote_proposal(outbox, context)
				outbox.status = "Published"
				outbox.next_attempt_at = None
			elif (
				context.active_dispatch_generation > 0
				and context.authoritative_generation in {0, context.active_dispatch_generation}
			):
				outbox.status = "Published"
				outbox.next_attempt_at = None
				_clear_proposal(outbox)
			else:
				_schedule_reconciliation(
					outbox,
					now=now,
					outcome="duplicate_active_generation_mismatch",
					error="COMPANION_ACTIVE_GENERATION_AHEAD",
					companion_generation=context.authoritative_generation,
				)
			outbox.attempt_count = context.previous_attempt_count
			outbox.callback_deadline_at = callback_deadline
			outbox.duplicate_active_observation_count = int(outbox.duplicate_active_observation_count or 0) + 1
		elif outcome == "callback_already_completed":
			status_data = query_companion_job_status(outbox)
			_repair_callback_completed_outbox(outbox, status_data, now=now)
			_clear_proposal(outbox)
		elif outcome in {"reconciliation_pending", "newer_generation_exists"}:
			outbox.attempt_count = context.previous_attempt_count
			_schedule_reconciliation(
				outbox,
				now=now,
				outcome=outcome,
				error="COMPANION_NEWER_GENERATION_EXISTS"
				if outcome == "newer_generation_exists"
				else "COMPANION_RECONCILIATION_PENDING",
				companion_generation=context.authoritative_generation,
			)
			_clear_proposal(outbox)
		else:
			raise OutboxError("Unsupported accepted companion outcome.")

		_clear_claim(outbox)
		if outcome not in {"callback_already_completed", "reconciliation_pending", "newer_generation_exists"}:
			outbox.last_error = None
		_update_dispatch_outcome_counters(outbox, context)
		_save_outbox(outbox)
		frappe.db.commit()
		return result
	except Exception as exc:
		if context.recovery_dispatch:
			_record_operational_event(outbox.service_type, "redispatch_failure")
			frappe.db.set_value(
				OUTBOX_DOCTYPE, outbox.name, "redispatch_failure_count",
				int(getattr(outbox, "redispatch_failure_count", 0) or 0) + 1, update_modified=False,
			)
		error_code = sanitized_dispatch_error(exc)
		if is_ambiguous_dispatch_error(exc):
			status = _mark_dispatch_uncertain(outbox.name, claim_token, error_code)
			_record_operational_event(outbox.service_type, "dispatch_uncertain")
		else:
			status = _mark_publish_failure(outbox.name, claim_token, error_code)
		frappe.db.commit()
		if status == "Dead Letter":
			frappe.log_error(frappe.get_traceback(), f"Transactional outbox dead-lettered: {outbox.name}")
		raise


def _constant_equal(left: str, right: str) -> bool:
	return bool(left and right) and hmac.compare_digest(
		hashlib.sha256(left.encode("utf-8")).digest(),
		hashlib.sha256(right.encode("utf-8")).digest(),
	)


def _callback_conflict(
	outbox: Any,
	message: str,
	*,
	error_code: str,
	event: str | None = None,
	counter_field: str | None = None,
) -> OutboxConflictError:
	if event:
		_record_operational_event(outbox.service_type, event)
	return OutboxConflictError(
		message,
		error_code=error_code,
		outbox_name=outbox.name,
		counter_field=counter_field,
	)


def validate_callback_idempotency(
	job: Any, payload: dict[str, Any], *, callback_status: str | None = None
) -> CallbackValidationResult:
	"""Validate callback correlation before any domain or durable-job mutation."""

	supplied = _clean(payload.get("idempotency_key"), limit=200)
	expected = _clean(getattr(job, "idempotency_key", None), limit=200)
	if supplied and not _constant_equal(supplied, expected):
		raise OutboxConflictError(
			"Callback idempotency key does not match the correlated job.",
			error_code="STABLE_DISPATCH_MISMATCH",
		)

	outbox_name = frappe.db.get_value(
		OUTBOX_DOCTYPE,
		{"job_doctype": getattr(job, "doctype", ""), "job_name": getattr(job, "name", "")},
		"name",
	)
	if not outbox_name:
		return CallbackValidationResult(duplicate=False, outbox_name=None)
	outbox = frappe.get_doc(OUTBOX_DOCTYPE, outbox_name)
	if not _constant_equal(supplied, expected):
		raise _callback_conflict(
			outbox,
			"Callback stable dispatch identifier does not match the durable job.",
			error_code="STABLE_DISPATCH_MISMATCH",
		)
	dispatch_id = _clean(payload.get("dispatch_id"), limit=200)
	if not _constant_equal(dispatch_id, _clean(outbox.idempotency_key, limit=200)):
		raise _callback_conflict(
			outbox,
			"Callback dispatch identifier does not match the outbox record.",
			error_code="STABLE_DISPATCH_MISMATCH",
		)
	try:
		supplied_generation = int(payload.get("dispatch_generation"))
	except (TypeError, ValueError):
		raise _callback_conflict(
			outbox,
			"Callback dispatch generation is invalid.",
			error_code="OLD_GENERATION_CALLBACK",
			event="old_generation_rejection",
			counter_field="old_generation_rejection_count",
		)
	supplied_token = _clean(payload.get("dispatch_token"), limit=140)
	incoming = _clean(callback_status or payload.get("status"), limit=120).lower()

	if outbox.status in FINAL_CALLBACK_STATUSES:
		completed_generation = int(outbox.completed_dispatch_generation or outbox.dispatch_generation or 0)
		last_token = _clean(outbox.last_callback_dispatch_token, limit=140)
		previous = _clean(outbox.callback_status, limit=120).lower()
		if supplied_generation != completed_generation:
			raise _callback_conflict(
				outbox,
				"Stale callback from an earlier completed dispatch generation was rejected.",
				error_code="OLD_GENERATION_CALLBACK",
				event="old_generation_rejection",
				counter_field="old_generation_rejection_count",
			)
		if not _constant_equal(supplied_token, last_token):
			raise _callback_conflict(
				outbox,
				"Callback token does not match the completed dispatch generation.",
				error_code="CALLBACK_TOKEN_MISMATCH",
				event="token_mismatch",
				counter_field="token_mismatch_count",
			)
		if incoming == previous:
			_ACTIVE_CALLBACK_CORRELATION.set(
				{
					"outbox_name": outbox.name,
					"dispatch_generation": supplied_generation,
					"dispatch_token": supplied_token,
					"matched_proposed": False,
				}
			)
			return CallbackValidationResult(duplicate=True, outbox_name=outbox.name)
		raise _callback_conflict(
			outbox,
			"Conflicting late callback rejected for completed callback generation.",
			error_code="TERMINAL_CALLBACK_CONFLICT",
		)

	if outbox.status not in {
		"Queued",
		"Claimed",
		"Dispatched",
		"Published",
		"Failed",
		"Dispatch Uncertain",
		"Reconciliation Pending",
		"Manual Review",
	}:
		raise _callback_conflict(
			outbox,
			"Outbox state does not permit a callback.",
			error_code="CALLBACK_STATE_CONFLICT",
		)
	current_generation = int(outbox.dispatch_generation or 0)
	current_token = _clean(outbox.current_dispatch_token, limit=140)
	proposed_generation = int(getattr(outbox, "proposed_dispatch_generation", 0) or 0)
	proposed_token = _clean(getattr(outbox, "proposed_dispatch_token", None), limit=140)
	matches_active = supplied_generation == current_generation and _constant_equal(supplied_token, current_token)
	matches_proposed = (
		proposed_generation > 0
		and supplied_generation == proposed_generation
		and _constant_equal(supplied_token, proposed_token)
	)
	if not matches_active and not matches_proposed:
		accepted_generations = accepted_dispatch_generations(
			current_generation=current_generation,
			proposed_generation=proposed_generation,
		)
		if supplied_generation not in accepted_generations:
			raise _callback_conflict(
				outbox,
				"Stale callback from an earlier dispatch generation was rejected.",
				error_code="OLD_GENERATION_CALLBACK",
				event="old_generation_rejection",
				counter_field="old_generation_rejection_count",
			)
		raise _callback_conflict(
			outbox,
			"Callback dispatch token does not match an accepted correlation state.",
			error_code="CALLBACK_TOKEN_MISMATCH",
			event="token_mismatch",
			counter_field="token_mismatch_count",
		)
	_ACTIVE_CALLBACK_CORRELATION.set(
		{
			"outbox_name": outbox.name,
			"dispatch_generation": supplied_generation,
			"dispatch_token": supplied_token,
			"matched_proposed": matches_proposed,
		}
	)
	return CallbackValidationResult(duplicate=False, outbox_name=outbox.name)


def complete_outbox_without_callback(*, job_doctype: str, job_name: str, status: str) -> Any | None:
	"""Complete an outbox record for deterministic local terminal outcomes."""

	return mark_outbox_callback(
		job_doctype=job_doctype,
		job_name=job_name,
		callback_status=status,
		success=True,
	)


def mark_outbox_callback(
	*,
	job_doctype: str,
	job_name: str,
	callback_status: str,
	success: bool,
	error: str | None = None,
	dispatch_token: str | None = None,
	dispatch_generation: int | None = None,
) -> Any | None:
	name = frappe.db.get_value(
		OUTBOX_DOCTYPE,
		{"job_doctype": job_doctype, "job_name": job_name},
		"name",
	)
	if not name:
		return None
	outbox = frappe.get_doc(OUTBOX_DOCTYPE, name)
	incoming = _clean(callback_status, limit=120).lower()
	previous = _clean(outbox.callback_status, limit=120).lower()
	if outbox.status in FINAL_CALLBACK_STATUSES:
		if previous == incoming:
			return outbox
		raise OutboxConflictError("Conflicting late callback rejected for terminal outbox record.")

	callback_time = now_datetime()
	correlation = _ACTIVE_CALLBACK_CORRELATION.get() or {}
	if correlation.get("outbox_name") == outbox.name:
		dispatch_token = dispatch_token or correlation.get("dispatch_token")
		dispatch_generation = dispatch_generation if dispatch_generation is not None else correlation.get("dispatch_generation")
	supplied_token = _clean(dispatch_token, limit=140)
	current_token = _clean(outbox.current_dispatch_token, limit=140)
	proposed_token = _clean(getattr(outbox, "proposed_dispatch_token", None), limit=140)
	if supplied_token and proposed_token and _constant_equal(supplied_token, proposed_token):
		outbox.dispatch_generation = int(dispatch_generation or outbox.proposed_dispatch_generation or 0)
		outbox.current_dispatch_token = proposed_token
		outbox.proposed_dispatch_generation = 0
		outbox.proposed_dispatch_token = None
		current_token = proposed_token
	if supplied_token and current_token and not hmac.compare_digest(supplied_token, current_token):
		if outbox.status in FINAL_CALLBACK_STATUSES:
			if previous == incoming:
				return outbox
			raise OutboxConflictError("Conflicting stale callback rejected for terminal outbox record.")
		raise OutboxConflictError("Stale callback from an earlier dispatch generation was rejected.")
	outbox.callback_received_at = callback_time
	outbox.last_callback_at = callback_time
	outbox.last_callback_dispatch_token = supplied_token or current_token or None
	outbox.completed_dispatch_generation = int(dispatch_generation or outbox.dispatch_generation or 0)
	outbox.callback_status = incoming
	outbox.callback_deadline_at = None
	outbox.claimed_by = None
	outbox.claim_token = None
	outbox.claimed_at = None
	outbox.lease_expires_at = None
	outbox.manual_review_reason = None
	if success:
		outbox.status = "Completed"
		outbox.completed_at = now_datetime()
		outbox.next_attempt_at = None
		outbox.last_error = None
		outbox.current_dispatch_token = None
	else:
		# A signed worker-failure callback represents a terminal work result.
		# Callback transport retries are independent and have already completed;
		# automatically redispatching this outbox would only replay cached failure.
		outbox.status = "Completed With Failure"
		outbox.completed_at = now_datetime()
		outbox.next_attempt_at = None
		outbox.last_error = _clean(error) or "Downstream service reported failure"
		outbox.current_dispatch_token = None
	_save_outbox(outbox)
	return outbox


def requeue_dead_letter_outbox(
	*,
	outbox_name: str,
	expected_idempotency_key: str,
	additional_attempts: int = 3,
) -> dict[str, Any]:
	"""Authorize a bounded replay of one dead-letter record.

	This is an explicit operator action, not a reusable domain helper. It
	intentionally commits after validating the exact persisted idempotency key
	so an operator cannot accidentally replay a similarly named record. The
	original row and attempt history remain intact.
	"""

	frappe.only_for("System Manager")
	name = _clean(outbox_name, limit=140)
	expected = _clean(expected_idempotency_key, limit=200)
	attempts_to_add = max(1, min(int(additional_attempts or 1), 10))
	if not name or not expected:
		raise OutboxError("Outbox name and expected idempotency key are required.")

	frappe.db.sql(
		f"SELECT name FROM `tab{OUTBOX_DOCTYPE}` WHERE name = %s FOR UPDATE",
		(name,),
	)
	outbox = frappe.get_doc(OUTBOX_DOCTYPE, name)
	if outbox.status != "Dead Letter":
		raise OutboxConflictError("Only a dead-letter outbox record can be replayed.")

	stored = _clean(outbox.idempotency_key, limit=200)
	if not stored or not hmac.compare_digest(
		hashlib.sha256(stored.encode("utf-8")).digest(),
		hashlib.sha256(expected.encode("utf-8")).digest(),
	):
		raise OutboxConflictError("Expected idempotency key does not match the outbox record.")

	current_attempts = int(outbox.attempt_count or 0)
	outbox.status = "Queued"
	outbox.pending_dispatch_reason = "operator_replay"
	outbox.max_attempts = max(int(outbox.max_attempts or 0), current_attempts + attempts_to_add)
	outbox.next_attempt_at = now_datetime()
	outbox.claimed_by = None
	outbox.claim_token = None
	outbox.claimed_at = None
	outbox.lease_expires_at = None
	outbox.callback_received_at = None
	outbox.callback_status = None
	outbox.callback_deadline_at = None
	outbox.current_dispatch_token = None
	outbox.proposed_dispatch_generation = 0
	outbox.proposed_dispatch_token = None
	outbox.completed_at = None
	outbox.last_error = None
	_save_outbox(outbox)
	frappe.db.commit()

	operator = _clean(getattr(getattr(frappe, "session", None), "user", "unknown"), limit=140)
	frappe.logger("aos.outbox", allow_site=True).warning(
		"Dead-letter replay authorized for outbox=%s service_type=%s operator=%s additional_attempts=%s",
		outbox.name,
		outbox.service_type,
		operator,
		attempts_to_add,
	)
	return {
		"outbox_name": outbox.name,
		"service_type": outbox.service_type,
		"status": outbox.status,
		"attempt_count": current_attempts,
		"max_attempts": int(outbox.max_attempts),
		"additional_attempts": attempts_to_add,
	}



def authorize_terminal_work_replay(
	*,
	outbox_name: str,
	expected_idempotency_key: str,
	additional_attempts: int = 3,
) -> dict[str, Any]:
	"""Explicitly authorize a new external-work execution after terminal failure.

	Callback replay is intentionally separate: this action archives/resets the
	companion terminal result and reopens the same durable Frappe job/outbox.
	It never creates a replacement service-job document.
	"""
	frappe.only_for("System Manager")
	name = _clean(outbox_name, limit=140)
	expected = _clean(expected_idempotency_key, limit=200)
	attempts_to_add = max(1, min(int(additional_attempts or 1), 10))

	frappe.db.sql(
		f"SELECT name FROM `tab{OUTBOX_DOCTYPE}` WHERE name = %s FOR UPDATE",
		(name,),
	)
	outbox = frappe.get_doc(OUTBOX_DOCTYPE, name)
	if outbox.status not in {"Completed With Failure", "Manual Review", "Dead Letter"}:
		raise OutboxConflictError("Only terminal failed or manual-review work can be replayed.")
	if not _constant_equal(expected, _clean(outbox.idempotency_key, limit=200)):
		raise OutboxConflictError("Outbox idempotency key confirmation failed.")

	job = frappe.get_doc(outbox.job_doctype, outbox.job_name)
	stable_job_id = downstream_idempotency_key(job, outbox.service_type)
	service_url, secret, header_name = _companion_status_config(outbox.service_type)
	from aos.api.shared.callback_security import build_signature

	body = _json_dumps({"job_id": outbox.job_name, "idempotency_key": stable_job_id}).encode("utf-8")
	response = requests.post(
		f"{service_url.rstrip('/')}/internal/jobs/work/replay",
		data=body,
		headers={"Content-Type": "application/json", header_name: build_signature(secret, body)},
		timeout=10,
	)
	response.raise_for_status()
	payload = response.json() if response.content else {}
	if not isinstance(payload, dict) or payload.get("outcome") != "work_replay_authorized":
		raise OutboxError("Companion did not authorize terminal work replay.")

	outbox.work_replay_count = int(getattr(outbox, "work_replay_count", 0) or 0) + 1
	outbox.status = "Queued"
	outbox.max_attempts = max(int(outbox.max_attempts or 1), int(outbox.attempt_count or 0) + attempts_to_add)
	outbox.next_attempt_at = now_datetime()
	outbox.pending_dispatch_reason = "operator_work_replay"
	outbox.active_dispatch_reason = None
	outbox.callback_status = None
	outbox.callback_received_at = None
	outbox.last_callback_at = None
	outbox.callback_deadline_at = None
	outbox.completed_at = None
	outbox.last_error = None
	outbox.manual_review_reason = None
	outbox.reconciliation_attempt_count = 0
	outbox.last_reconciliation_outcome = "operator_work_replay_authorized"
	_clear_claim(outbox)
	_clear_proposal(outbox)
	_save_outbox(outbox)

	job.status = "Queued"
	if hasattr(job, "last_error"):
		job.last_error = None
	if hasattr(job, "completed_at"):
		job.completed_at = None
	job.flags.ignore_links = True
	job.save(ignore_permissions=True)
	frappe.db.commit()
	return {
		"outbox_name": outbox.name,
		"service_type": outbox.service_type,
		"status": outbox.status,
		"work_replay_count": int(outbox.work_replay_count or 0),
		"additional_attempts": attempts_to_add,
	}


def outbox_monitoring_summary() -> dict[str, Any]:
	counts = frappe.db.sql(
		f"SELECT status, COUNT(*) AS total FROM `tab{OUTBOX_DOCTYPE}` GROUP BY status",
		as_dict=True,
	)
	counts_by_status = {_clean(row.status, limit=80): int(row.total or 0) for row in counts}
	now = now_datetime()
	oldest = frappe.db.sql(
		f"""
        SELECT TIMESTAMPDIFF(SECOND, MIN(creation), %s)
        FROM `tab{OUTBOX_DOCTYPE}`
        WHERE status IN ('Queued', 'Failed', 'Dispatch Uncertain', 'Reconciliation Pending')
        """,
		(now,),
	)[0][0]
	stale = frappe.db.sql(
		f"""
        SELECT COUNT(*) FROM `tab{OUTBOX_DOCTYPE}`
	        WHERE claim_token IS NOT NULL
	          AND lease_expires_at IS NOT NULL AND lease_expires_at < %s
        """,
		(now,),
	)[0][0]
	overdue_callbacks = frappe.db.sql(
		f"""
        SELECT COUNT(*) FROM `tab{OUTBOX_DOCTYPE}`
        WHERE status = 'Published'
          AND callback_deadline_at IS NOT NULL AND callback_deadline_at < %s
        """,
		(now,),
	)[0][0]
	by_service = frappe.db.sql(
		f"""
        SELECT service_type, status, COUNT(*) AS total
        FROM `tab{OUTBOX_DOCTYPE}`
        GROUP BY service_type, status
        """,
		as_dict=True,
	)
	lifecycle_rows = frappe.db.sql(
		f"""
        SELECT service_type,
               COUNT(*) AS created_total,
               SUM(attempt_count) AS dispatched_total,
	               SUM(CASE WHEN status IN ('Completed', 'Completed With Failure') THEN 1 ELSE 0 END) AS completed_total,
               SUM(GREATEST(attempt_count - 1, 0)
	                   + CASE WHEN status IN ('Failed', 'Completed With Failure', 'Manual Review', 'Dead Letter') THEN 1 ELSE 0 END) AS failed_total,
               SUM(GREATEST(attempt_count - 1, 0)) AS retried_total,
               SUM(CASE WHEN status = 'Dead Letter' THEN 1 ELSE 0 END) AS dead_lettered_total,
               SUM(callback_timeout_count) AS callback_timeouts_total,
               SUM(redispatch_accepted_count) AS redispatch_accepted_total,
               SUM(redispatch_skipped_count) AS redispatch_skipped_total,
               SUM(redispatch_failure_count) AS redispatch_failure_total,
               SUM(duplicate_active_dispatch_count) AS duplicate_active_dispatch_total,
               SUM(callback_replay_count) AS callback_replay_total,
               SUM(old_generation_rejection_count) AS old_generation_rejection_total,
               SUM(token_mismatch_count) AS token_mismatch_total,
               SUM(transaction_rollback_count) AS transaction_rollback_total,
               SUM(CASE WHEN completed_at IS NOT NULL THEN
                   GREATEST(TIMESTAMPDIFF(MICROSECOND, created_at, completed_at) / 1000000.0, 0) ELSE 0 END)
                   AS duration_seconds_sum,
               SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS duration_seconds_count
        FROM `tab{OUTBOX_DOCTYPE}`
        GROUP BY service_type
        """,
		as_dict=True,
	)
	services: dict[str, dict[str, int]] = {service: {} for service in SERVICE_TYPES}
	for row in by_service:
		services.setdefault(_clean(row.service_type, limit=80), {})[_clean(row.status, limit=80)] = int(
			row.total or 0
		)
	lifecycle: dict[str, dict[str, float | int]] = {service: {} for service in SERVICE_TYPES}
	for row in lifecycle_rows:
		service = _clean(row.service_type, limit=80)
		lifecycle[service] = {
			"created_total": int(row.created_total or 0),
			"dispatched_total": int(row.dispatched_total or 0),
			"completed_total": int(row.completed_total or 0),
			"failed_total": int(row.failed_total or 0),
			"retried_total": int(row.retried_total or 0),
			"dead_lettered_total": int(row.dead_lettered_total or 0),
			"callback_timeouts_total": int(row.callback_timeouts_total or 0),
			"redispatch_accepted_total": int(row.redispatch_accepted_total or 0),
			"redispatch_skipped_total": int(row.redispatch_skipped_total or 0),
			"redispatch_failure_total": int(row.redispatch_failure_total or 0),
			"duplicate_active_dispatch_total": int(row.duplicate_active_dispatch_total or 0),
			"callback_replay_total": int(row.callback_replay_total or 0),
			"old_generation_rejection_total": int(row.old_generation_rejection_total or 0),
			"token_mismatch_total": int(row.token_mismatch_total or 0),
			"transaction_rollback_total": int(row.transaction_rollback_total or 0),
			"duration_seconds_sum": float(row.duration_seconds_sum or 0),
			"duration_seconds_count": int(row.duration_seconds_count or 0),
		}
	return {
		"counts_by_status": counts_by_status,
		"queue_depth": sum(
			counts_by_status.get(status, 0)
			for status in ("Queued", "Failed", "Dispatch Uncertain", "Reconciliation Pending")
		),
		"claimed_count": int(
			frappe.db.sql(
				f"SELECT COUNT(*) FROM `tab{OUTBOX_DOCTYPE}` WHERE claim_token IS NOT NULL",
			)[0][0]
			or 0
		),
		"stale_lease_count": int(stale or 0),
		"callback_overdue_count": int(overdue_callbacks or 0),
		"oldest_queued_age_seconds": int(oldest or 0),
		"dead_letter_count": counts_by_status.get("Dead Letter", 0),
		"manual_review_count": counts_by_status.get("Manual Review", 0),
		"by_service": services,
		"lifecycle_by_service": lifecycle,
	}
