"""Admin-facing job monitoring for AOS background work.

The checks in this module are intentionally read-only. They summarize Frappe
queue state and AOS external-service job doctypes without exposing payloads,
tracebacks, request bodies, tokens, secrets, or raw upstream error messages.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import frappe
from frappe.utils import add_to_date, cint, now_datetime

from aos.api.shared.public_errors import is_sensitive_exception_message
from aos.utils import aos_config

JobMonitorStatus = str


@dataclass(frozen=True)
class ServiceJobSpec:
	name: str
	doctype: str
	category: str
	active_statuses: tuple[str, ...]
	failed_statuses: tuple[str, ...] = ("Failed",)
	terminal_statuses: tuple[str, ...] = ()
	enabled_env: str | None = None
	default_enabled: bool = True


SERVICE_JOB_SPECS: tuple[ServiceJobSpec, ...] = (
	ServiceJobSpec(
		name="video_processing_jobs",
		doctype="AOS Video Processing Job",
		category="video_processing",
		active_statuses=("Queued", "Dispatching", "Processing"),
		terminal_statuses=("Ready", "Failed", "Cancelled"),
	),
	ServiceJobSpec(
		name="moderation_jobs",
		doctype="AOS Moderation Job",
		category="moderation",
		active_statuses=("Queued", "Dispatching", "Processing"),
		terminal_statuses=("Allowed", "Review Required", "Rejected", "Failed", "Cancelled"),
		enabled_env="MODERATION_ENABLED",
	),
	ServiceJobSpec(
		name="search_ranking_jobs",
		doctype="AOS Search Index Job",
		category="search_ranking",
		active_statuses=("Queued", "Dispatching", "Processing"),
		terminal_statuses=("Indexed", "Deleted", "Failed", "Cancelled"),
		enabled_env="SEARCH_RANKING_ENABLED",
	),
	ServiceJobSpec(
		name="analytics_pipeline_jobs",
		doctype="AOS Analytics Ingest Job",
		category="analytics_pipeline",
		active_statuses=("Queued", "Dispatching", "Processing"),
		terminal_statuses=("Ingested", "Skipped", "Failed", "Cancelled"),
		enabled_env="ANALYTICS_PIPELINE_ENABLED",
	),
	ServiceJobSpec(
		name="notification_delivery_jobs",
		doctype="AOS Notification Delivery Job",
		category="notification_delivery",
		active_statuses=("Queued", "Dispatching", "Processing"),
		terminal_statuses=("Delivered", "Skipped", "Failed", "Cancelled"),
		enabled_env="NOTIFICATION_DELIVERY_ENABLED",
	),
)

DEFAULT_MONITORED_QUEUES: tuple[str, ...] = ("short", "default", "long")

SERVICE_QUEUE_ENV_KEYS: tuple[str, ...] = (
	"VIDEO_FRAPPE_QUEUE",
	"MODERATION_FRAPPE_QUEUE",
	"SEARCH_RANKING_FRAPPE_QUEUE",
	"ANALYTICS_FRAPPE_QUEUE",
	"NOTIFICATION_FRAPPE_QUEUE",
)


def _clean(value: Any) -> str:
	return str(value or "").strip()


def _bool_env(env: Mapping[str, Any] | None, key: str, default: bool) -> bool:
	value = _clean((env or {}).get(key) if env is not None else aos_config.get_env(key))
	if not value:
		return bool(default)
	return value.lower() in {"1", "true", "yes", "y", "on"}


def _env(env: Mapping[str, Any] | None, key: str, default: str = "") -> str:
	if env is not None:
		return _clean(env.get(key) or default)
	return _clean(aos_config.get_env(key, default) or default)


def _safe_int(value: Any) -> int:
	try:
		return max(0, int(value or 0))
	except Exception:
		return 0


def _check(
	checks: list[dict[str, Any]],
	*,
	name: str,
	category: str,
	status: JobMonitorStatus,
	message: str,
	details: Mapping[str, Any] | None = None,
) -> None:
	checks.append(
		{
			"name": name,
			"category": category,
			"status": status,
			"message": message,
			"details": dict(details or {}),
		}
	)


def _limited_names(values: list[Any] | tuple[Any, ...], limit: int = 5) -> list[str]:
	safe: list[str] = []
	for value in list(values or [])[: max(0, limit)]:
		text = _clean(value)
		if not text:
			continue
		if is_sensitive_exception_message(text):
			safe.append("[redacted]")
		else:
			safe.append(text[:140])
	return safe


def _status_from_service_job_stats(stats: Mapping[str, Any]) -> tuple[JobMonitorStatus, str]:
	stale_count = _safe_int(stats.get("stale_active_count"))
	long_running_count = _safe_int(stats.get("long_running_count"))
	retry_risk_count = _safe_int(stats.get("retry_risk_count"))
	failed_count = _safe_int(stats.get("failed_count"))

	if stale_count or long_running_count:
		return "unhealthy", "Some active service jobs are stale or long-running."
	if retry_risk_count:
		return "degraded", "Some service jobs are near or past their retry limit."
	if failed_count:
		return "degraded", "Some service jobs failed recently or remain failed."
	return "healthy", "Service jobs are flowing normally."


def _service_job_check_from_stats(stats: Mapping[str, Any]) -> dict[str, Any]:
	status, message = _status_from_service_job_stats(stats)
	counts_by_status = {
		_clean(key): _safe_int(value)
		for key, value in dict(stats.get("counts_by_status") or {}).items()
		if _clean(key)
	}
	details = {
		"doctype": _clean(stats.get("doctype")),
		"counts_by_status": counts_by_status,
		"active_count": _safe_int(stats.get("active_count")),
		"failed_count": _safe_int(stats.get("failed_count")),
		"stale_active_count": _safe_int(stats.get("stale_active_count")),
		"long_running_count": _safe_int(stats.get("long_running_count")),
		"retry_risk_count": _safe_int(stats.get("retry_risk_count")),
		"stale_threshold_minutes": _safe_int(stats.get("stale_threshold_minutes")),
		"long_running_threshold_minutes": _safe_int(stats.get("long_running_threshold_minutes")),
		"sample_failed_jobs": _limited_names(list(stats.get("sample_failed_jobs") or [])),
		"sample_stale_jobs": _limited_names(list(stats.get("sample_stale_jobs") or [])),
	}
	if stats.get("enabled") is False:
		return {
			"name": _clean(stats.get("name")),
			"category": _clean(stats.get("category")) or "service_job",
			"status": "skipped",
			"message": "Service job monitor is disabled by configuration.",
			"details": {"doctype": details["doctype"]},
		}
	return {
		"name": _clean(stats.get("name")),
		"category": _clean(stats.get("category")) or "service_job",
		"status": status,
		"message": message,
		"details": details,
	}


def _doctype_exists(doctype: str) -> bool:
	try:
		return bool(frappe.db.exists("DocType", doctype))
	except Exception:
		return False


def _sql_in_placeholders(values: tuple[str, ...]) -> str:
	return ", ".join(["%s"] * len(values)) or "%s"


def _collect_one_service_job_stats(
	spec: ServiceJobSpec,
	*,
	env: Mapping[str, Any] | None,
	stale_threshold_minutes: int,
	long_running_threshold_minutes: int,
) -> dict[str, Any]:
	enabled = True
	if spec.enabled_env:
		enabled = _bool_env(env, spec.enabled_env, spec.default_enabled)
	base = {
		"name": spec.name,
		"doctype": spec.doctype,
		"category": spec.category,
		"enabled": enabled,
		"counts_by_status": {},
		"active_count": 0,
		"failed_count": 0,
		"stale_active_count": 0,
		"long_running_count": 0,
		"retry_risk_count": 0,
		"stale_threshold_minutes": stale_threshold_minutes,
		"long_running_threshold_minutes": long_running_threshold_minutes,
		"sample_failed_jobs": [],
		"sample_stale_jobs": [],
	}
	if not enabled:
		return base
	if not _doctype_exists(spec.doctype):
		return {
			**base,
			"stale_active_count": 1,
			"sample_stale_jobs": ["doctype-missing"],
		}

	table = f"`tab{spec.doctype}`"
	counts = frappe.db.sql(
		f"SELECT status, COUNT(*) AS count FROM {table} GROUP BY status",
		as_dict=True,
	)
	counts_by_status = {
		_clean(row.get("status")): _safe_int(row.get("count")) for row in counts if _clean(row.get("status"))
	}

	stale_cutoff = add_to_date(now_datetime(), minutes=-stale_threshold_minutes)
	long_running_cutoff = add_to_date(now_datetime(), minutes=-long_running_threshold_minutes)

	active_placeholders = _sql_in_placeholders(spec.active_statuses)
	failed_placeholders = _sql_in_placeholders(spec.failed_statuses)

	active_count = frappe.db.sql(
		f"SELECT COUNT(*) FROM {table} WHERE status IN ({active_placeholders})",
		spec.active_statuses,
	)[0][0]
	failed_count = frappe.db.sql(
		f"SELECT COUNT(*) FROM {table} WHERE status IN ({failed_placeholders})",
		spec.failed_statuses,
	)[0][0]
	stale_active_count = frappe.db.sql(
		f"""
        SELECT COUNT(*)
        FROM {table}
        WHERE status IN ({active_placeholders})
          AND COALESCE(started_at, dispatched_at, modified, creation) < %s
        """,
		(*spec.active_statuses, stale_cutoff),
	)[0][0]
	long_running_count = frappe.db.sql(
		f"""
        SELECT COUNT(*)
        FROM {table}
        WHERE status = 'Processing'
          AND COALESCE(started_at, modified, creation) < %s
        """,
		(long_running_cutoff,),
	)[0][0]
	retry_risk_count = frappe.db.sql(
		f"""
        SELECT COUNT(*)
        FROM {table}
        WHERE status IN ({active_placeholders})
          AND COALESCE(max_attempts, 0) > 0
          AND COALESCE(attempt_count, 0) >= COALESCE(max_attempts, 0)
        """,
		spec.active_statuses,
	)[0][0]
	sample_failed = frappe.db.sql(
		f"""
        SELECT name
        FROM {table}
        WHERE status IN ({failed_placeholders})
        ORDER BY modified DESC
        LIMIT 5
        """,
		spec.failed_statuses,
		pluck=True,
	)
	sample_stale = frappe.db.sql(
		f"""
        SELECT name
        FROM {table}
        WHERE status IN ({active_placeholders})
          AND COALESCE(started_at, dispatched_at, modified, creation) < %s
        ORDER BY COALESCE(started_at, dispatched_at, modified, creation) ASC
        LIMIT 5
        """,
		(*spec.active_statuses, stale_cutoff),
		pluck=True,
	)

	return {
		**base,
		"counts_by_status": counts_by_status,
		"active_count": active_count,
		"failed_count": failed_count,
		"stale_active_count": stale_active_count,
		"long_running_count": long_running_count,
		"retry_risk_count": retry_risk_count,
		"sample_failed_jobs": sample_failed,
		"sample_stale_jobs": sample_stale,
	}


def _collect_service_job_stats(
	*,
	env: Mapping[str, Any] | None,
	stale_threshold_minutes: int,
	long_running_threshold_minutes: int,
) -> list[dict[str, Any]]:
	return [
		_collect_one_service_job_stats(
			spec,
			env=env,
			stale_threshold_minutes=stale_threshold_minutes,
			long_running_threshold_minutes=long_running_threshold_minutes,
		)
		for spec in SERVICE_JOB_SPECS
	]


def _status_from_queue_stats(
	stats: Mapping[str, Any], *, backlog_warn: int, backlog_unhealthy: int
) -> tuple[JobMonitorStatus, str]:
	queued = _safe_int(stats.get("queued_count"))
	failed = _safe_int(stats.get("failed_count"))
	started = _safe_int(stats.get("started_count"))
	if queued >= backlog_unhealthy:
		return "unhealthy", "Queue backlog is above the unhealthy threshold."
	if queued >= backlog_warn:
		return "degraded", "Queue backlog is above the warning threshold."
	if failed:
		return "degraded", "Queue has failed jobs in its failed-job registry."
	if started >= backlog_warn:
		return "degraded", "Queue has a high number of started jobs."
	return "healthy", "Queue backlog is within expected limits."


def _queue_check_from_stats(
	stats: Mapping[str, Any], *, backlog_warn: int, backlog_unhealthy: int
) -> dict[str, Any]:
	if stats.get("status") == "unhealthy":
		return {
			"name": f"frappe_queue:{_clean(stats.get('queue')) or 'unknown'}",
			"category": "frappe_queue",
			"status": "unhealthy",
			"message": "Queue could not be inspected.",
			"details": {"queue": _clean(stats.get("queue")) or "unknown"},
		}
	status, message = _status_from_queue_stats(
		stats, backlog_warn=backlog_warn, backlog_unhealthy=backlog_unhealthy
	)
	queue = _clean(stats.get("queue")) or "unknown"
	return {
		"name": f"frappe_queue:{queue}",
		"category": "frappe_queue",
		"status": status,
		"message": message,
		"details": {
			"queue": queue,
			"queued_count": _safe_int(stats.get("queued_count")),
			"failed_count": _safe_int(stats.get("failed_count")),
			"started_count": _safe_int(stats.get("started_count")),
			"scheduled_count": _safe_int(stats.get("scheduled_count")),
			"deferred_count": _safe_int(stats.get("deferred_count")),
			"backlog_warning_threshold": backlog_warn,
			"backlog_unhealthy_threshold": backlog_unhealthy,
		},
	}


def _monitored_queues(env: Mapping[str, Any] | None) -> list[str]:
	queues = list(DEFAULT_MONITORED_QUEUES)
	for key in SERVICE_QUEUE_ENV_KEYS:
		value = _env(env, key, "")
		if value and value not in queues:
			queues.append(value)
	return queues


def _collect_queue_stats(*, env: Mapping[str, Any] | None) -> list[dict[str, Any]]:
	try:
		from frappe.utils.background_jobs import get_redis_conn
		from rq import Queue
		from rq.registry import (
			DeferredJobRegistry,
			FailedJobRegistry,
			ScheduledJobRegistry,
			StartedJobRegistry,
		)

		conn = get_redis_conn()
	except Exception:
		return [{"queue": "all", "status": "unhealthy"}]

	def registry_count(registry_cls: Any, queue: Any) -> int:
		try:
			return len(registry_cls(queue=queue))
		except TypeError:
			try:
				return len(registry_cls(queue.name, connection=conn))
			except Exception:
				return 0
		except Exception:
			return 0

	rows: list[dict[str, Any]] = []
	for queue_name in _monitored_queues(env):
		try:
			queue = Queue(queue_name, connection=conn)
			rows.append(
				{
					"queue": queue_name,
					"queued_count": len(queue),
					"failed_count": registry_count(FailedJobRegistry, queue),
					"started_count": registry_count(StartedJobRegistry, queue),
					"scheduled_count": registry_count(ScheduledJobRegistry, queue),
					"deferred_count": registry_count(DeferredJobRegistry, queue),
				}
			)
		except Exception:
			rows.append({"queue": queue_name, "status": "unhealthy"})
	return rows


def _background_error_check_from_stats(stats: Mapping[str, Any]) -> dict[str, Any]:
	count = _safe_int(stats.get("error_count"))
	status = "degraded" if count else "healthy"
	message = (
		"Recent background job errors were found." if count else "No recent background job errors were found."
	)
	return {
		"name": "frappe_background_job_errors",
		"category": "background_jobs",
		"status": status,
		"message": message,
		"details": {
			"error_count": count,
			"window_hours": _safe_int(stats.get("window_hours")),
			"sample_methods": _limited_names(list(stats.get("sample_methods") or [])),
		},
	}


def _collect_background_error_stats(*, window_hours: int) -> dict[str, Any]:
	cutoff = add_to_date(now_datetime(), hours=-window_hours)
	try:
		count = frappe.db.sql(
			"""
            SELECT COUNT(*)
            FROM `tabError Log`
            WHERE creation >= %s
              AND (
                error LIKE %s
                OR method LIKE %s
                OR method LIKE %s
              )
            """,
			(cutoff, '%"type": "background_job"%', "%aos.tasks%", "%dispatch_%"),
		)[0][0]
		methods = frappe.db.sql(
			"""
            SELECT method
            FROM `tabError Log`
            WHERE creation >= %s
              AND (
                error LIKE %s
                OR method LIKE %s
                OR method LIKE %s
              )
            ORDER BY creation DESC
            LIMIT 5
            """,
			(cutoff, '%"type": "background_job"%', "%aos.tasks%", "%dispatch_%"),
			pluck=True,
		)
	except Exception:
		return {"error_count": 1, "window_hours": window_hours, "sample_methods": ["error-log-query-failed"]}
	return {"error_count": count, "window_hours": window_hours, "sample_methods": methods}


def validate_job_monitoring(
	*,
	env: Mapping[str, Any] | None = None,
	stale_threshold_minutes: int = 30,
	long_running_threshold_minutes: int = 30,
	background_error_window_hours: int = 24,
	queue_backlog_warning: int = 1000,
	queue_backlog_unhealthy: int = 10000,
	service_job_stats_provider: Callable[..., list[dict[str, Any]]] | None = None,
	queue_stats_provider: Callable[..., list[dict[str, Any]]] | None = None,
	background_error_provider: Callable[..., dict[str, Any]] | None = None,
	outbox_summary_provider: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
	"""Return a redacted job-monitoring report for AOS production operations."""

	stale_minutes = max(1, min(cint(stale_threshold_minutes) or 30, 24 * 60))
	long_minutes = max(1, min(cint(long_running_threshold_minutes) or 30, 24 * 60))
	window_hours = max(1, min(cint(background_error_window_hours) or 24, 7 * 24))
	backlog_warn = max(1, cint(queue_backlog_warning) or 1000)
	backlog_unhealthy = max(backlog_warn + 1, cint(queue_backlog_unhealthy) or 10000)

	checks: list[dict[str, Any]] = []
	service_provider = service_job_stats_provider or _collect_service_job_stats
	queue_provider = queue_stats_provider or _collect_queue_stats
	error_provider = background_error_provider or _collect_background_error_stats

	try:
		service_stats = service_provider(
			env=env,
			stale_threshold_minutes=stale_minutes,
			long_running_threshold_minutes=long_minutes,
		)
	except TypeError:
		service_stats = service_provider()
	except Exception:
		service_stats = [
			{
				"name": "aos_service_jobs",
				"doctype": "AOS Service Jobs",
				"category": "service_job",
				"stale_active_count": 1,
				"stale_threshold_minutes": stale_minutes,
				"long_running_threshold_minutes": long_minutes,
				"sample_stale_jobs": ["service-job-query-failed"],
			}
		]
	for stats in service_stats:
		checks.append(_service_job_check_from_stats(stats))

	try:
		queue_stats = queue_provider(env=env)
	except TypeError:
		queue_stats = queue_provider()
	except Exception:
		queue_stats = [{"queue": "all", "status": "unhealthy"}]
	for stats in queue_stats:
		checks.append(
			_queue_check_from_stats(
				stats,
				backlog_warn=backlog_warn,
				backlog_unhealthy=backlog_unhealthy,
			)
		)

	try:
		error_stats = error_provider(window_hours=window_hours)
	except TypeError:
		error_stats = error_provider()
	except Exception:
		error_stats = {
			"error_count": 1,
			"window_hours": window_hours,
			"sample_methods": ["error-log-query-failed"],
		}
	checks.append(_background_error_check_from_stats(error_stats))

	try:
		if outbox_summary_provider is None:
			from aos.services.transactional_outbox import outbox_monitoring_summary

			outbox_provider = outbox_monitoring_summary
		else:
			outbox_provider = outbox_summary_provider
		outbox = outbox_provider()
		dead_letters = _safe_int(outbox.get("dead_letter_count"))
		manual_reviews = _safe_int(outbox.get("manual_review_count"))
		stale_leases = _safe_int(outbox.get("stale_lease_count"))
		oldest_age = _safe_int(outbox.get("oldest_queued_age_seconds"))
		queue_depth = _safe_int(outbox.get("queue_depth"))
		if dead_letters or manual_reviews or stale_leases or oldest_age > stale_minutes * 60:
			outbox_status = "unhealthy"
			outbox_message = (
				"Transactional outbox has terminal review records, stale leases, or overdue records."
			)
		elif queue_depth > backlog_warn:
			outbox_status = "degraded"
			outbox_message = "Transactional outbox backlog is elevated."
		else:
			outbox_status = "healthy"
			outbox_message = "Transactional outbox recovery is healthy."
		checks.append(
			{
				"name": "transactional_outbox",
				"category": "service_job",
				"status": outbox_status,
				"message": outbox_message,
				"details": {
					"queue_depth": queue_depth,
					"claimed_count": _safe_int(outbox.get("claimed_count")),
					"stale_lease_count": stale_leases,
					"oldest_queued_age_seconds": oldest_age,
					"dead_letter_count": dead_letters,
					"manual_review_count": manual_reviews,
					"service_types": sorted((outbox.get("by_service") or {}).keys()),
				},
			}
		)
	except Exception:
		checks.append(
			{
				"name": "transactional_outbox",
				"category": "service_job",
				"status": "unhealthy",
				"message": "Transactional outbox monitoring query failed.",
				"details": {},
			}
		)

	counts = {
		"healthy": 0,
		"degraded": 0,
		"unhealthy": 0,
		"skipped": 0,
		"checks": len(checks),
	}
	for check in checks:
		status = _clean(check.get("status")) or "unhealthy"
		if status not in counts:
			status = "unhealthy"
		counts[status] += 1

	return {
		"ready": counts["unhealthy"] == 0,
		"summary": counts,
		"checks": checks,
	}


def job_monitoring_summary() -> dict[str, Any]:
	"""Bench-friendly job-monitoring report."""

	return validate_job_monitoring()


def assert_job_monitoring_ready() -> dict[str, Any]:
	"""Raise when durable jobs or queues have unhealthy monitoring findings."""
	report = validate_job_monitoring()
	if not report.get("ready"):
		unhealthy = int((report.get("summary") or {}).get("unhealthy") or 0)
		raise RuntimeError(f"AOS job monitoring is not ready: {unhealthy} unhealthy check(s).")
	return report
