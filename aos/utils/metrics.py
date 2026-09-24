"""Prometheus/OpenMetrics operational metrics for the Frappe backend.

Labels are deliberately bounded to reviewed enums. User identifiers, document
IDs, request paths, bodies, URLs, filenames, and secrets are never labels.
"""

from __future__ import annotations

import hmac
import os
import shutil
import time
from collections import Counter
from threading import Lock
from typing import Any

import frappe

from aos.services.media.media_purposes import MEDIA_PURPOSES

_LOCK = Lock()
_REQUESTS: Counter[tuple[str, str, str]] = Counter()
_DURATIONS: Counter[tuple[str, str]] = Counter()
_DURATION_SUM: Counter[tuple[str, str]] = Counter()
_EXCEPTIONS: Counter[str] = Counter()
_RATE_LIMIT_REJECTIONS: Counter[str] = Counter()
_MEDIA_EVENTS: Counter[tuple[str, str, str]] = Counter()
_ACCOUNT_EVENTS: Counter[tuple[str, str]] = Counter()
_CATALOG_EVENTS: Counter[tuple[str, str]] = Counter()
_CALL_EVENTS: Counter[tuple[str, str]] = Counter()
_MEDIA_BYTES: Counter[str] = Counter()
_MEDIA_DURATION_COUNT: Counter[str] = Counter()
_MEDIA_DURATION_SUM: Counter[str] = Counter()
_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_DURATION_BUCKETS: Counter[tuple[str, str, float]] = Counter()
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
_ALLOWED_SURFACES = {"api", "website", "health", "metrics", "other"}
_METRIC_PREFIX = os.getenv("AOS_METRICS_REDIS_PREFIX", "aos:operational:metrics:v1").strip() or "aos:operational:metrics:v1"
_REDIS_KEYS = {
	"requests": f"{_METRIC_PREFIX}:http:requests",
	"duration_count": f"{_METRIC_PREFIX}:http:duration_count",
	"duration_sum": f"{_METRIC_PREFIX}:http:duration_sum",
	"duration_buckets": f"{_METRIC_PREFIX}:http:duration_buckets",
	"exceptions": f"{_METRIC_PREFIX}:exceptions",
	"rate_limits": f"{_METRIC_PREFIX}:rate_limits",
	"outbox_events": f"{_METRIC_PREFIX}:outbox_events",
	"media_events": f"{_METRIC_PREFIX}:media:events",
	"media_bytes": f"{_METRIC_PREFIX}:media:bytes",
	"media_duration_count": f"{_METRIC_PREFIX}:media:duration_count",
	"media_duration_sum": f"{_METRIC_PREFIX}:media:duration_sum",
	"account_events": f"{_METRIC_PREFIX}:accounts:events",
	"catalog_events": f"{_METRIC_PREFIX}:catalog:events",
	"call_events": f"{_METRIC_PREFIX}:calls:events",
}


def _production() -> bool:
	return str(os.getenv("AOS_ENVIRONMENT", "development")).strip().lower() == "production"


def _allow_process_fallback() -> bool:
	return not _production() and str(os.getenv("AOS_METRICS_ALLOW_PROCESS_FALLBACK", "true")).lower() in {
		"1", "true", "yes", "on"
	}


def _redis_cache():
	return frappe.cache()


def _decode_hash(raw: Any) -> dict[str, float]:
	result: dict[str, float] = {}
	for key, value in (raw or {}).items():
		name = key.decode("utf-8") if isinstance(key, bytes) else str(key)
		text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
		try:
			result[name] = float(text)
		except (TypeError, ValueError):
			continue
	return result


def _redis_increment(key: str, field: str, amount: float = 1) -> bool:
	try:
		cache = _redis_cache()
		if isinstance(amount, float) and not amount.is_integer():
			cache.hincrbyfloat(key, field, amount)
		else:
			cache.hincrby(key, field, int(amount))
		return True
	except Exception:
		return False



_ALLOWED_OUTBOX_SERVICES = {
	"video_processing", "moderation", "search_indexing",
	"notification_delivery", "analytics_ingestion",
}
_ALLOWED_OUTBOX_EVENTS = {
	"callback_retry", "redispatch_accepted", "redispatch_skipped",
	"redispatch_failure", "duplicate_active_dispatch", "callback_replay",
	"old_generation_rejection", "token_mismatch", "transaction_rollback",
	"reconciliation_exhausted", "automatic_outbox_repair",
	"terminal_work_failure", "failure_callback_completed",
	"callback_during_lease", "generation_advanced", "manual_review",
}


def record_outbox_event(service_type: str, event: str, amount: int = 1) -> None:
	service = str(service_type or "").strip().lower()
	event_name = str(event or "").strip().lower()
	if service not in _ALLOWED_OUTBOX_SERVICES or event_name not in _ALLOWED_OUTBOX_EVENTS:
		return
	_redis_increment(_REDIS_KEYS["outbox_events"], f"{service}|{event_name}", max(1, int(amount or 1)))



_ALLOWED_ACCOUNT_EVENTS = {
    "account.profile.read", "account.profile.updated", "account.preference.updated",
    "account.avatar.replaced", "account.avatar.removed", "account.bootstrap.completed",
    "account.bootstrap.failed", "account.deactivation.requested", "account.deactivated",
    "account.deletion.requested", "account.deleted", "account.restored",
}
_ALLOWED_ACCOUNT_OUTCOMES = {"success", "rejected", "failure"}


def record_account_event(*, event: str, outcome: str = "success", amount: int = 1) -> None:
    event_name = str(event or "").strip().lower()
    outcome_name = str(outcome or "success").strip().lower()
    if event_name not in _ALLOWED_ACCOUNT_EVENTS or outcome_name not in _ALLOWED_ACCOUNT_OUTCOMES:
        return
    ok = _redis_increment(_REDIS_KEYS["account_events"], f"{event_name}|{outcome_name}", max(1, int(amount or 1)))
    if not ok and _allow_process_fallback():
        with _LOCK:
            _ACCOUNT_EVENTS[(event_name, outcome_name)] += max(1, int(amount or 1))


def _safe_account_metrics(lines: list[str]) -> None:
    try:
        raw = _decode_hash(_redis_cache().hgetall(_REDIS_KEYS["account_events"]))
    except Exception:
        with _LOCK:
            raw = {"|".join(key): value for key, value in _ACCOUNT_EVENTS.items()}
    lines.extend([
        "# HELP aos_account_events_total Bounded account lifecycle and profile events.",
        "# TYPE aos_account_events_total counter",
    ])
    for key, value in sorted(raw.items()):
        parts = key.split("|", 1)
        if len(parts) != 2:
            continue
        event_name, outcome_name = parts
        if event_name in _ALLOWED_ACCOUNT_EVENTS and outcome_name in _ALLOWED_ACCOUNT_OUTCOMES:
            lines.append(_line("aos_account_events_total", int(value), {"event": event_name, "outcome": outcome_name}))


_ALLOWED_CALL_EVENTS = {
    "initiate_call", "mark_call_ringing", "accept_call", "reject_call", "cancel_call",
    "end_call", "request_video_upgrade", "respond_video_upgrade", "get_call_status",
    "get_call_token", "list_calls", "add_call_participants", "delete_call_logs",
    "clear_call_history", "token_issue", "room_cleanup", "room_cleanup_enqueue",
    "room_provision", "room_provision_enqueue", "room_provision_finalize", "incoming_dispatch",
    "active_room_reconcile", "active_policy_reconcile", "active_reconcile", "missed", "timeout",
}
_ALLOWED_CALL_OUTCOMES = {"success", "failure", "rejected", "conflict", "idempotent"}


def record_call_event(*, event: str, outcome: str = "success", amount: int = 1) -> None:
    """Record bounded Calls operational events without user/call identifiers."""
    event_name = str(event or "").strip().lower()
    outcome_name = str(outcome or "success").strip().lower()
    if event_name not in _ALLOWED_CALL_EVENTS or outcome_name not in _ALLOWED_CALL_OUTCOMES:
        return
    increment = max(1, int(amount or 1))
    ok = _redis_increment(_REDIS_KEYS["call_events"], f"{event_name}|{outcome_name}", increment)
    if not ok and _allow_process_fallback():
        with _LOCK:
            _CALL_EVENTS[(event_name, outcome_name)] += increment


def _safe_call_metrics(lines: list[str]) -> None:
    try:
        raw = _decode_hash(_redis_cache().hgetall(_REDIS_KEYS["call_events"]))
    except Exception:
        with _LOCK:
            raw = {"|".join(key): value for key, value in _CALL_EVENTS.items()}
    lines.extend([
        "# HELP aos_call_events_total Bounded Calls lifecycle and operational events.",
        "# TYPE aos_call_events_total counter",
    ])
    for key, value in sorted(raw.items()):
        parts = key.split("|", 1)
        if len(parts) != 2:
            continue
        event_name, outcome_name = parts
        if event_name in _ALLOWED_CALL_EVENTS and outcome_name in _ALLOWED_CALL_OUTCOMES:
            lines.append(_line("aos_call_events_total", int(value), {"event": event_name, "outcome": outcome_name}))


_ALLOWED_CATALOG_EVENTS = {
    "categories_read",
    "schema_read",
    "attribute_options_read",
    "configuration_changed",
    "configuration_rejected",
}
_ALLOWED_CATALOG_OUTCOMES = {"success", "not_found", "rejected", "failure"}


def record_catalog_event(*, event: str, outcome: str = "success", amount: int = 1) -> None:
    event_name = str(event or "").strip().lower()
    outcome_name = str(outcome or "success").strip().lower()
    if event_name not in _ALLOWED_CATALOG_EVENTS or outcome_name not in _ALLOWED_CATALOG_OUTCOMES:
        return
    ok = _redis_increment(
        _REDIS_KEYS["catalog_events"],
        f"{event_name}|{outcome_name}",
        max(1, int(amount or 1)),
    )
    if not ok and _allow_process_fallback():
        with _LOCK:
            _CATALOG_EVENTS[(event_name, outcome_name)] += max(1, int(amount or 1))


def _safe_catalog_metrics(lines: list[str]) -> None:
    try:
        raw = _decode_hash(_redis_cache().hgetall(_REDIS_KEYS["catalog_events"]))
    except Exception:
        with _LOCK:
            raw = {"|".join(key): value for key, value in _CATALOG_EVENTS.items()}
    lines.extend([
        "# HELP aos_catalog_events_total Bounded Catalog reads and configuration events.",
        "# TYPE aos_catalog_events_total counter",
    ])
    for key, value in sorted(raw.items()):
        parts = key.split("|", 1)
        if len(parts) != 2:
            continue
        event_name, outcome_name = parts
        if event_name in _ALLOWED_CATALOG_EVENTS and outcome_name in _ALLOWED_CATALOG_OUTCOMES:
            lines.append(
                _line(
                    "aos_catalog_events_total",
                    int(value),
                    {"event": event_name, "outcome": outcome_name},
                )
            )


# Keep operational metric labels bounded to the authoritative Media purpose registry.
# `unknown` is the sole synthetic bucket for rejected/unrecognized purpose values.
_ALLOWED_MEDIA_PURPOSES = frozenset({*MEDIA_PURPOSES.keys(), "unknown"})
_ALLOWED_MEDIA_EVENTS = {
	"upload_initiated", "upload_completed", "upload_rejected", "upload_failed",
	"attachment_completed", "replacement_completed", "delete_requested", "delete_completed",
	"delete_failed", "cleanup_completed", "processing_started", "processing_completed",
	"processing_failed", "storage_operation",
}
_ALLOWED_MEDIA_OUTCOMES = {"success", "rejected", "retryable_failure", "failure"}


def record_media_event(
	*,
	event: str,
	purpose: str,
	outcome: str = "success",
	bytes_count: int | None = None,
	duration_seconds: float | None = None,
) -> None:
	"""Record bounded Media metrics; never use IDs, paths, filenames, or errors as labels."""
	event_name = str(event or "").strip().lower()
	purpose_name = str(purpose or "unknown").strip().lower()
	outcome_name = str(outcome or "success").strip().lower()
	if event_name not in _ALLOWED_MEDIA_EVENTS:
		return
	if purpose_name not in _ALLOWED_MEDIA_PURPOSES:
		purpose_name = "unknown"
	if outcome_name not in _ALLOWED_MEDIA_OUTCOMES:
		outcome_name = "failure"
	field = f"{event_name}|{purpose_name}|{outcome_name}"
	ok = _redis_increment(_REDIS_KEYS["media_events"], field)
	if bytes_count is not None:
		ok = _redis_increment(
			_REDIS_KEYS["media_bytes"], purpose_name, max(0, int(bytes_count or 0))
		) and ok
	if duration_seconds is not None:
		duration = max(0.0, float(duration_seconds or 0.0))
		ok = _redis_increment(_REDIS_KEYS["media_duration_count"], event_name) and ok
		ok = _redis_increment(_REDIS_KEYS["media_duration_sum"], event_name, duration) and ok
	if not ok and _allow_process_fallback():
		with _LOCK:
			_MEDIA_EVENTS[(event_name, purpose_name, outcome_name)] += 1
			if bytes_count is not None:
				_MEDIA_BYTES[purpose_name] += max(0, int(bytes_count or 0))
			if duration_seconds is not None:
				_MEDIA_DURATION_COUNT[event_name] += 1
				_MEDIA_DURATION_SUM[event_name] += max(0.0, float(duration_seconds or 0.0))


def _safe_media_metrics(lines: list[str]) -> None:
	try:
		cache = _redis_cache()
		raw_events = _decode_hash(cache.hgetall(_REDIS_KEYS["media_events"]))
		raw_bytes = _decode_hash(cache.hgetall(_REDIS_KEYS["media_bytes"]))
		raw_counts = _decode_hash(cache.hgetall(_REDIS_KEYS["media_duration_count"]))
		raw_sums = _decode_hash(cache.hgetall(_REDIS_KEYS["media_duration_sum"]))
	except Exception:
		with _LOCK:
			raw_events = {"|".join(key): value for key, value in _MEDIA_EVENTS.items()}
			raw_bytes = dict(_MEDIA_BYTES)
			raw_counts = dict(_MEDIA_DURATION_COUNT)
			raw_sums = dict(_MEDIA_DURATION_SUM)
	lines.extend([
		"# HELP aos_media_events_total Media lifecycle events by bounded purpose and outcome.",
		"# TYPE aos_media_events_total counter",
	])
	for key, value in sorted(raw_events.items()):
		parts = key.split("|")
		if len(parts) != 3:
			continue
		event_name, purpose_name, outcome_name = parts
		if (
			event_name not in _ALLOWED_MEDIA_EVENTS
			or purpose_name not in _ALLOWED_MEDIA_PURPOSES
			or outcome_name not in _ALLOWED_MEDIA_OUTCOMES
		):
			continue
		lines.append(_line(
			"aos_media_events_total", int(value),
			{"event": event_name, "purpose": purpose_name, "outcome": outcome_name},
		))
	lines.extend([
		"# HELP aos_media_bytes_total Verified media bytes by bounded purpose.",
		"# TYPE aos_media_bytes_total counter",
	])
	for purpose_name, value in sorted(raw_bytes.items()):
		if purpose_name in _ALLOWED_MEDIA_PURPOSES:
			lines.append(_line("aos_media_bytes_total", int(value), {"purpose": purpose_name}))
	lines.extend([
		"# HELP aos_media_operation_duration_seconds Media operation duration by bounded event.",
		"# TYPE aos_media_operation_duration_seconds summary",
	])
	for event_name, count in sorted(raw_counts.items()):
		if event_name not in _ALLOWED_MEDIA_EVENTS:
			continue
		labels = {"event": event_name}
		lines.append(_line(
			"aos_media_operation_duration_seconds_sum",
			f"{float(raw_sums.get(event_name, 0.0)):.6f}", labels,
		))
		lines.append(_line("aos_media_operation_duration_seconds_count", int(count), labels))


def _surface() -> str:
	try:
		path = str(getattr(frappe.local.request, "path", "") or "")
	except Exception:
		path = ""
	if "metrics" in path:
		return "metrics"
	if "health" in path or "ready" in path or "diagnostic" in path:
		return "health"
	if path.startswith("/api/"):
		return "api"
	if path:
		return "website"
	return "other"


def _method() -> str:
	try:
		value = str(getattr(frappe.local.request, "method", "") or "").upper()
	except Exception:
		value = ""
	return value if value in _ALLOWED_METHODS else "OTHER"


def _status_code() -> int:
	try:
		response = frappe.local.response
		return int(response.get("http_status_code") or response.get("status_code") or 200)
	except Exception:
		return 200


def before_request() -> None:
	frappe.local.aos_metrics_started = time.perf_counter()


def after_request() -> None:
	started = float(getattr(frappe.local, "aos_metrics_started", time.perf_counter()))
	duration = max(0.0, time.perf_counter() - started)
	method = _method()
	surface = _surface()
	code = _status_code()
	status_class = f"{max(0, min(code // 100, 9))}xx"
	request_field = "|".join((method, surface, status_class))
	duration_field = "|".join((method, surface))
	ok = _redis_increment(_REDIS_KEYS["requests"], request_field)
	ok = _redis_increment(_REDIS_KEYS["duration_count"], duration_field) and ok
	ok = _redis_increment(_REDIS_KEYS["duration_sum"], duration_field, duration) and ok
	for bucket in _BUCKETS:
		if duration <= bucket:
			ok = _redis_increment(
				_REDIS_KEYS["duration_buckets"], f"{duration_field}|{bucket:g}"
			) and ok
	if code >= 500:
		ok = _redis_increment(_REDIS_KEYS["exceptions"], "http_5xx") and ok
	if not ok and _allow_process_fallback():
		with _LOCK:
			_REQUESTS[(method, surface, status_class)] += 1
			_DURATIONS[(method, surface)] += 1
			_DURATION_SUM[(method, surface)] += duration
			for bucket in _BUCKETS:
				if duration <= bucket:
					_DURATION_BUCKETS[(method, surface, bucket)] += 1
			if code >= 500:
				_EXCEPTIONS["http_5xx"] += 1


def on_error(*_args: Any, **_kwargs: Any) -> None:
	if not _redis_increment(_REDIS_KEYS["exceptions"], "unhandled") and _allow_process_fallback():
		with _LOCK:
			_EXCEPTIONS["unhandled"] += 1


def record_rate_limit_rejection(policy: str = "application") -> None:
	label = str(policy or "application").strip().lower()
	if label not in {"application", "gateway", "authentication", "guest", "sensitive"}:
		label = "application"
	if not _redis_increment(_REDIS_KEYS["rate_limits"], label) and _allow_process_fallback():
		with _LOCK:
			_RATE_LIMIT_REJECTIONS[label] += 1


def _escape(value: Any) -> str:
	return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _line(name: str, value: Any, labels: dict[str, str] | None = None) -> str:
	if labels:
		rendered = ",".join(f'{key}="{_escape(val)}"' for key, val in sorted(labels.items()))
		return f"{name}{{{rendered}}} {value}"
	return f"{name} {value}"


def _check_status_value(status: str) -> int:
	return 1 if str(status or "").lower() == "healthy" else 0


def _safe_outbox_metrics(lines: list[str]) -> None:
	try:
		from aos.services.transactional_outbox import outbox_monitoring_summary

		summary = outbox_monitoring_summary()
	except Exception:
		summary = {
			"counts_by_status": {},
			"queue_depth": 0,
			"claimed_count": 0,
			"stale_lease_count": 0,
			"oldest_queued_age_seconds": 0,
			"dead_letter_count": 0,
			"by_service": {},
		}
	lines += [
		"# HELP aos_background_queue_depth Durable outbox records due for publication.",
		"# TYPE aos_background_queue_depth gauge",
		_line("aos_background_queue_depth", int(summary.get("queue_depth") or 0)),
		"# HELP aos_background_oldest_queued_job_age_seconds Age of the oldest due outbox record.",
		"# TYPE aos_background_oldest_queued_job_age_seconds gauge",
		_line(
			"aos_background_oldest_queued_job_age_seconds", int(summary.get("oldest_queued_age_seconds") or 0)
		),
		"# HELP aos_background_claimed_jobs Current claimed/dispatched outbox records.",
		"# TYPE aos_background_claimed_jobs gauge",
		_line("aos_background_claimed_jobs", int(summary.get("claimed_count") or 0)),
		"# HELP aos_background_stale_leases Current expired outbox claims.",
		"# TYPE aos_background_stale_leases gauge",
		_line("aos_background_stale_leases", int(summary.get("stale_lease_count") or 0)),
		"# HELP aos_background_callback_overdue Published outbox records past their callback deadline.",
		"# TYPE aos_background_callback_overdue gauge",
		_line("aos_background_callback_overdue", int(summary.get("callback_overdue_count") or 0)),
		"# HELP aos_background_dead_letter_jobs Current outbox dead-letter records.",
		"# TYPE aos_background_dead_letter_jobs gauge",
		_line("aos_background_dead_letter_jobs", int(summary.get("dead_letter_count") or 0)),
		"# HELP aos_background_manual_review_jobs Current outbox records requiring bounded operator review.",
		"# TYPE aos_background_manual_review_jobs gauge",
		_line("aos_background_manual_review_jobs", int(summary.get("manual_review_count") or 0)),
		"# HELP aos_background_jobs Jobs by reviewed service type and lifecycle state.",
		"# TYPE aos_background_jobs gauge",
	]
	allowed_states = {
		"Queued",
		"Published",
		"Dispatch Uncertain",
		"Reconciliation Pending",
		"Completed",
		"Completed With Failure",
		"Failed",
		"Manual Review",
		"Dead Letter",
		"Cancelled",
	}
	for service, states in sorted((summary.get("by_service") or {}).items()):
		for state, count in sorted((states or {}).items()):
			if (
				service
				in {
					"video_processing",
					"moderation",
					"search_indexing",
					"notification_delivery",
					"analytics_ingestion",
				}
				and state in allowed_states
			):
				lines.append(
					_line(
						"aos_background_jobs",
						int(count or 0),
						{"service": service, "state": state.lower().replace(" ", "_")},
					)
				)

	lifecycle_metrics = {
		"created_total": "aos_background_jobs_created_total",
		"dispatched_total": "aos_background_jobs_dispatched_total",
		"completed_total": "aos_background_jobs_completed_total",
		"failed_total": "aos_background_jobs_failed_total",
		"retried_total": "aos_background_jobs_retried_total",
		"dead_lettered_total": "aos_background_jobs_dead_lettered_total",
		"callback_timeouts_total": "aos_background_callback_timeouts_total",
		"redispatch_accepted_total": "aos_background_redispatch_accepted_total",
		"redispatch_skipped_total": "aos_background_redispatch_skipped_total",
		"redispatch_failure_total": "aos_background_redispatch_failures_total",
		"duplicate_active_dispatch_total": "aos_background_duplicate_active_dispatch_total",
		"callback_replay_total": "aos_background_callback_replay_total",
		"old_generation_rejection_total": "aos_background_old_generation_callback_rejections_total",
		"token_mismatch_total": "aos_background_callback_token_mismatches_total",
		"transaction_rollback_total": "aos_background_callback_transaction_rollbacks_total",
	}
	for field, metric in lifecycle_metrics.items():
		lines.extend(
			[f"# HELP {metric} Durable outbox lifecycle total by service.", f"# TYPE {metric} counter"]
		)
		for service, values in sorted((summary.get("lifecycle_by_service") or {}).items()):
			if service in {
				"video_processing",
				"moderation",
				"search_indexing",
				"notification_delivery",
				"analytics_ingestion",
			}:
				lines.append(_line(metric, int((values or {}).get(field) or 0), {"service": service}))
	lines.extend(
		[
			"# HELP aos_background_job_duration_seconds Durable job terminal duration by service.",
			"# TYPE aos_background_job_duration_seconds summary",
		]
	)
	for service, values in sorted((summary.get("lifecycle_by_service") or {}).items()):
		if service in {
			"video_processing",
			"moderation",
			"search_indexing",
			"notification_delivery",
			"analytics_ingestion",
		}:
			labels = {"service": service}
			lines.append(
				_line(
					"aos_background_job_duration_seconds_sum",
					f"{float((values or {}).get('duration_seconds_sum') or 0):.6f}",
					labels,
				)
			)
			lines.append(
				_line(
					"aos_background_job_duration_seconds_count",
					int((values or {}).get("duration_seconds_count") or 0),
					labels,
				)
			)

	try:
		raw_events = _decode_hash(_redis_cache().hgetall(_REDIS_KEYS["outbox_events"]))
	except Exception:
		raw_events = {}
	lines.extend([
		"# HELP aos_background_outbox_events_total Callback recovery and transaction events by bounded service type.",
		"# TYPE aos_background_outbox_events_total counter",
	])
	for key, value in sorted(raw_events.items()):
		parts = key.split("|", 1)
		if len(parts) != 2 or parts[0] not in _ALLOWED_OUTBOX_SERVICES or parts[1] not in _ALLOWED_OUTBOX_EVENTS:
			continue
		lines.append(_line(
			"aos_background_outbox_events_total", int(value),
			{"service": parts[0], "event": parts[1]},
		))


def _safe_dependency_metrics(lines: list[str]) -> None:
	try:
		from aos.utils.operational_health import validate_operational_health

		report = validate_operational_health(timeout_seconds=2)
		checks = report.get("checks") or []
	except Exception:
		checks = []
	reviewed = {
		"frappe_redis": "redis",
		"database": "database",
		"minio": "minio",
		"livekit_health": "livekit",
		"video_processing_ready": "video_processing",
		"moderation_ready": "moderation",
		"search_ranking_ready": "search_ranking",
		"notification_delivery_ready": "notification_delivery",
		"analytics_pipeline_ready": "analytics_pipeline",
	}
	values = {label: 0 for label in set(reviewed.values())}
	for check in checks:
		label = reviewed.get(str(check.get("name") or ""))
		if label:
			values[label] = _check_status_value(str(check.get("status") or ""))
	lines += ["# HELP aos_dependency_ready Dependency readiness.", "# TYPE aos_dependency_ready gauge"]
	for dependency, value in sorted(values.items()):
		lines.append(_line("aos_dependency_ready", value, {"dependency": dependency}))


def _safe_backup_metrics(lines: list[str]) -> None:
	try:
		from aos.utils.backup_readiness import validate_backup_readiness

		report = validate_backup_readiness()
		checks = {str(item.get("name")): item for item in report.get("checks") or []}
	except Exception:
		report, checks = {"ready": False}, {}
	latest = checks.get("latest_backup_artifact", {})
	offsite = checks.get("offsite_backup_scope", {})
	encryption = checks.get("backup_encryption", {})
	rehearsal = checks.get("restore_rehearsal", {})
	latest_details = latest.get("details") or {}
	rehearsal_details = rehearsal.get("details") or {}
	backup_age_hours = float(latest_details.get("latest_backup_age_hours") or 0)
	rehearsal_age_days = float(rehearsal_details.get("restore_rehearsal_age_days") or 0)
	lines += [
		"# HELP aos_backup_readiness Backup readiness gate.",
		"# TYPE aos_backup_readiness gauge",
		_line("aos_backup_readiness", int(bool(report.get("ready")))),
		"# HELP aos_backup_age_seconds Age of latest verified backup.",
		"# TYPE aos_backup_age_seconds gauge",
		_line("aos_backup_age_seconds", max(0, backup_age_hours * 3600)),
		"# HELP aos_backup_verification_status Latest backup verification status.",
		"# TYPE aos_backup_verification_status gauge",
		_line("aos_backup_verification_status", _check_status_value(latest.get("status"))),
		"# HELP aos_backup_offsite_status Latest offsite-copy status.",
		"# TYPE aos_backup_offsite_status gauge",
		_line("aos_backup_offsite_status", _check_status_value(offsite.get("status"))),
		"# HELP aos_backup_encryption_status Backup encryption readiness.",
		"# TYPE aos_backup_encryption_status gauge",
		_line("aos_backup_encryption_status", _check_status_value(encryption.get("status"))),
		"# HELP aos_restore_rehearsal_status Latest full restore rehearsal status.",
		"# TYPE aos_restore_rehearsal_status gauge",
		_line("aos_restore_rehearsal_status", _check_status_value(rehearsal.get("status"))),
		"# HELP aos_restore_rehearsal_age_seconds Age of latest full restore rehearsal.",
		"# TYPE aos_restore_rehearsal_age_seconds gauge",
		_line("aos_restore_rehearsal_age_seconds", max(0, rehearsal_age_days * 86400)),
	]


def _safe_config_metrics(lines: list[str]) -> None:
	try:
		from aos.utils.production_config import validate_production_config

		report = validate_production_config()
		errors = int((report.get("summary") or {}).get("errors") or 0)
	except Exception:
		report, errors = {"ready": False}, 1
	lines += [
		"# HELP aos_production_readiness_status Production configuration readiness.",
		"# TYPE aos_production_readiness_status gauge",
		_line("aos_production_readiness_status", int(bool(report.get("ready")))),
		"# HELP aos_missing_required_configuration Missing or invalid required production configuration count.",
		"# TYPE aos_missing_required_configuration gauge",
		_line("aos_missing_required_configuration", errors),
	]


def _http_snapshot() -> tuple[dict, dict, dict, dict, dict, dict, int]:
	try:
		cache = _redis_cache()
		raw_requests = _decode_hash(cache.hgetall(_REDIS_KEYS["requests"]))
		raw_counts = _decode_hash(cache.hgetall(_REDIS_KEYS["duration_count"]))
		raw_sums = _decode_hash(cache.hgetall(_REDIS_KEYS["duration_sum"]))
		raw_buckets = _decode_hash(cache.hgetall(_REDIS_KEYS["duration_buckets"]))
		raw_exceptions = _decode_hash(cache.hgetall(_REDIS_KEYS["exceptions"]))
		raw_limits = _decode_hash(cache.hgetall(_REDIS_KEYS["rate_limits"]))
		requests = {tuple(k.split("|", 2)): int(v) for k, v in raw_requests.items() if len(k.split("|", 2)) == 3}
		counts = {tuple(k.split("|", 1)): int(v) for k, v in raw_counts.items() if len(k.split("|", 1)) == 2}
		sums = {tuple(k.split("|", 1)): float(v) for k, v in raw_sums.items() if len(k.split("|", 1)) == 2}
		buckets = {}
		for key, value in raw_buckets.items():
			parts = key.split("|")
			if len(parts) == 3:
				try:
					buckets[(parts[0], parts[1], float(parts[2]))] = int(value)
				except ValueError:
					pass
		return requests, counts, sums, buckets, {k: int(v) for k, v in raw_exceptions.items()}, {k: int(v) for k, v in raw_limits.items()}, 1
	except Exception:
		if _production():
			return {}, {}, {}, {}, {}, {}, 0
		with _LOCK:
			return dict(_REQUESTS), dict(_DURATIONS), dict(_DURATION_SUM), dict(_DURATION_BUCKETS), dict(_EXCEPTIONS), dict(_RATE_LIMIT_REJECTIONS), 0


def render_metrics() -> str:
	requests, duration_count, duration_sum, duration_buckets, exceptions, rejections, backend_ready = _http_snapshot()
	lines = [
		"# HELP aos_metrics_backend_ready Redis-backed cross-worker metric aggregation readiness.",
		"# TYPE aos_metrics_backend_ready gauge",
		_line("aos_metrics_backend_ready", backend_ready),
		"# HELP aos_http_requests_total HTTP requests by method, surface, and status class.",
		"# TYPE aos_http_requests_total counter",
	]
	for (method, surface, status_class), count in sorted(requests.items()):
		lines.append(
			_line(
				"aos_http_requests_total",
				count,
				{"method": method, "surface": surface, "status_class": status_class},
			)
		)
	lines += [
		"# HELP aos_http_request_duration_seconds HTTP request duration.",
		"# TYPE aos_http_request_duration_seconds histogram",
	]
	for (method, surface), count in sorted(duration_count.items()):
		for bucket in _BUCKETS:
			lines.append(
				_line(
					"aos_http_request_duration_seconds_bucket",
					duration_buckets.get((method, surface, bucket), 0),
					{"method": method, "surface": surface, "le": f"{bucket:g}"},
				)
			)
		lines.append(
			_line(
				"aos_http_request_duration_seconds_bucket",
				count,
				{"method": method, "surface": surface, "le": "+Inf"},
			)
		)
		lines.append(
			_line(
				"aos_http_request_duration_seconds_sum",
				f"{duration_sum.get((method, surface), 0.0):.9f}",
				{"method": method, "surface": surface},
			)
		)
		lines.append(
			_line("aos_http_request_duration_seconds_count", count, {"method": method, "surface": surface})
		)
	lines += [
		"# HELP aos_unhandled_exceptions_total Unhandled backend exceptions by sanitized category.",
		"# TYPE aos_unhandled_exceptions_total counter",
	]
	for category, count in sorted(exceptions.items()):
		lines.append(_line("aos_unhandled_exceptions_total", count, {"category": category}))
	lines += [
		"# HELP aos_rate_limit_rejections_total Rate-limit rejections by reviewed policy class.",
		"# TYPE aos_rate_limit_rejections_total counter",
	]
	for policy, count in sorted(rejections.items()):
		lines.append(_line("aos_rate_limit_rejections_total", count, {"policy": policy}))
	_safe_outbox_metrics(lines)
	_safe_dependency_metrics(lines)
	_safe_backup_metrics(lines)
	_safe_config_metrics(lines)
	_safe_media_metrics(lines)
	_safe_account_metrics(lines)
	_safe_catalog_metrics(lines)
	_safe_call_metrics(lines)
	try:
		free = shutil.disk_usage(os.getenv("AOS_DISK_METRICS_PATH", "/")).free
	except Exception:
		free = 0
	lines += [
		"# HELP aos_disk_free_bytes Free bytes on the monitored filesystem.",
		"# TYPE aos_disk_free_bytes gauge",
		_line("aos_disk_free_bytes", int(free)),
	]
	return "\n".join(lines) + "\n# EOF\n"


def render_background_metrics() -> str:
	lines: list[str] = []
	_safe_outbox_metrics(lines)
	return "\n".join(lines) + "\n# EOF\n"


def render_backup_metrics() -> str:
	lines: list[str] = []
	_safe_backup_metrics(lines)
	_safe_config_metrics(lines)
	return "\n".join(lines) + "\n# EOF\n"


def metrics_access_allowed() -> bool:
	token = str(os.getenv("AOS_METRICS_TOKEN", "") or "").strip()
	try:
		authorization = str(frappe.get_request_header("Authorization") or "")
		supplied = (
			authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
		)
		remote = str(getattr(frappe.local, "request_ip", "") or "")
	except Exception:
		supplied, remote = "", ""
	if token and supplied and hmac.compare_digest(token, supplied):
		return True
	allow_loopback = str(os.getenv("AOS_METRICS_ALLOW_LOOPBACK", "true")).lower() in {
		"1",
		"true",
		"yes",
		"on",
	}
	return allow_loopback and remote in {"127.0.0.1", "::1", "localhost"}
