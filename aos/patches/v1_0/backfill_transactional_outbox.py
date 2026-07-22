"""Backfill retryable legacy external-service jobs into the transactional outbox."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from aos.services.transactional_outbox import (
	OUTBOX_DOCTYPE,
	ensure_outbox_for_job,
	stable_idempotency_key,
)

BATCH_SIZE = 200


@dataclass(frozen=True)
class LegacyJobSpec:
	service_type: str
	doctype: str
	completed_statuses: frozenset[str]
	permanent_statuses: frozenset[str]
	published_statuses: frozenset[str]
	queue: str
	timeout_seconds: int


SPECS = (
	LegacyJobSpec("video_processing", "AOS Video Processing Job", frozenset({"Ready"}), frozenset({"Cancelled"}), frozenset({"Processing"}), "long", 3600),
	LegacyJobSpec("moderation", "AOS Moderation Job", frozenset({"Allowed", "Review Required", "Rejected"}), frozenset({"Cancelled"}), frozenset({"Processing"}), "long", 900),
	LegacyJobSpec("search_indexing", "AOS Search Index Job", frozenset({"Indexed", "Deleted"}), frozenset({"Cancelled"}), frozenset({"Processing"}), "short", 600),
	LegacyJobSpec("notification_delivery", "AOS Notification Delivery Job", frozenset({"Delivered", "Skipped"}), frozenset({"Cancelled"}), frozenset({"Processing"}), "short", 600),
	LegacyJobSpec("analytics_ingestion", "AOS Analytics Ingest Job", frozenset({"Ingested", "Skipped"}), frozenset({"Cancelled"}), frozenset({"Processing"}), "short", 600),
)


def _clean(value: Any, limit: int = 1000) -> str:
	return str(value or "").strip()[:limit]


def _row_value(row: Any, fieldname: str, default: Any = None) -> Any:
	"""Read Frappe dict rows and simple object test doubles uniformly."""
	if isinstance(row, dict):
		return row.get(fieldname, default)
	getter = getattr(row, "get", None)
	if callable(getter):
		try:
			return getter(fieldname, default)
		except TypeError:
			value = getter(fieldname)
			return default if value is None else value
	return getattr(row, fieldname, default)


def _clear_stale_aggregate_link(outbox: Any) -> None:
	"""Remove aggregate metadata that points at a deleted historical record.

	The durable job correlation remains authoritative. Aggregate links are
	optional observability metadata and must not make a legacy migration fail
	when the original domain object was already deleted.
	"""
	doctype = getattr(outbox, "aggregate_doctype", None)
	name = getattr(outbox, "aggregate_name", None)
	if doctype in (None, "") and name in (None, ""):
		return
	if not isinstance(doctype, str) or not isinstance(name, str):
		return
	doctype = _clean(doctype, 140)
	name = _clean(name, 140)
	if not doctype or not name:
		outbox.aggregate_doctype = None
		outbox.aggregate_name = None
		return
	if not frappe.db.exists("DocType", doctype) or not frappe.db.exists(doctype, name):
		outbox.aggregate_doctype = None
		outbox.aggregate_name = None


def _payload_malformed(value: Any) -> bool:
	text = _clean(value, 100000)
	if not text:
		return False
	try:
		json.loads(text)
		return False
	except Exception:
		return True


def _should_backfill(status: str, attempts: int, maximum: int, spec: LegacyJobSpec) -> bool:
	if status in spec.completed_statuses or status in spec.permanent_statuses:
		return False
	if status == "Failed" and attempts >= maximum:
		return False
	return status in {"Queued", "Dispatching", "Processing", "Failed"}


def _backfill_job(spec: LegacyJobSpec, row: Any, counters: dict[str, int]) -> None:
	status = _clean(_row_value(row, "status"), 80)
	attempts = max(0, int(_row_value(row, "attempt_count") or 0))
	maximum = max(1, int(_row_value(row, "max_attempts") or 3))
	if not _should_backfill(status, attempts, maximum, spec):
		counters["skipped_terminal"] += 1
		return

	job = frappe.get_doc(spec.doctype, _row_value(row, "name"))
	counters["malformed_payload"] += int(
		_payload_malformed(getattr(job, "request_payload", None))
	)

	generated_key = stable_idempotency_key(spec.service_type, spec.doctype, job.name)
	current_key = _clean(getattr(job, "idempotency_key", None), 200)
	legacy_service_job_id = (
		_clean(getattr(job, "service_job_id", None), 200)
		if status in spec.published_statuses
		else ""
	)
	if not current_key or current_key == generated_key:
		idempotency_key = legacy_service_job_id or generated_key
		# Preserve an existing companion/RQ stable ID for legacy Processing
		# work. This lets the signed status endpoint find queued, started, or
		# finished legacy jobs without rerunning their external side effect.
		frappe.db.set_value(spec.doctype, job.name, "idempotency_key", idempotency_key, update_modified=False)
		job.idempotency_key = idempotency_key

	existing = frappe.db.get_value(
		OUTBOX_DOCTYPE,
		{"job_doctype": spec.doctype, "job_name": job.name},
		"name",
	)
	if existing:
		# The migration is a creator/backfill, not a live-state reconciler.
		# Once a durable outbox exists, its status, attempts, lease, and error
		# history are authoritative and must never be reset by a patch rerun.
		counters["existing"] += 1
		return

	outbox = ensure_outbox_for_job(
		service_type=spec.service_type,
		job=job,
		queue=spec.queue,
		timeout_seconds=spec.timeout_seconds,
		max_attempts=maximum,
	)
	outbox.attempt_count = attempts
	outbox.max_attempts = maximum
	outbox.last_error = "LEGACY_RETRYABLE_FAILURE" if _clean(getattr(job, "last_error", None)) else None
	if status == "Failed":
		outbox.status = "Failed"
		outbox.next_attempt_at = now_datetime()
	elif status in spec.published_statuses and _clean(getattr(job, "service_job_id", None), 200):
		# Legacy Processing workers may predate generation/token correlation and
		# may have completed work while callback delivery failed. Reconcile by
		# stable companion identity instead of pretending the row is fully Published.
		published = get_datetime(getattr(job, "dispatched_at", None) or getattr(job, "modified", None) or now_datetime())
		outbox.status = "Reconciliation Pending"
		outbox.published_at = published
		outbox.pending_dispatch_reason = "legacy_processing_reconciliation"
		outbox.callback_deadline_at = None
		outbox.next_attempt_at = now_datetime()
		outbox.max_attempts = max(int(outbox.max_attempts or 0), attempts + 3)
	else:
		outbox.status = "Queued"
		outbox.next_attempt_at = now_datetime()
	_clear_stale_aggregate_link(outbox)
	outbox.save(ignore_permissions=True)

	counters["created"] += 1


def execute(batch_size: int = BATCH_SIZE) -> dict[str, int]:
	"""Run inside the normal patch transaction; no helper commits are performed."""

	batch_size = max(10, min(int(batch_size or BATCH_SIZE), 1000))
	counters = {
		"scanned": 0,
		"created": 0,
		"existing": 0,
		"skipped_terminal": 0,
		"malformed_payload": 0,
		"missing_table": 0,
	}
	for spec in SPECS:
		if not frappe.db.table_exists(spec.doctype):
			counters["missing_table"] += 1
			continue
		cursor = ""
		while True:
			rows = frappe.db.sql(
				f"""
                SELECT name, status, attempt_count, max_attempts
                FROM `tab{spec.doctype}`
                WHERE name > %s
                ORDER BY name ASC
                LIMIT %s
                """,
				(cursor, batch_size),
				as_dict=True,
			)
			if not rows:
				break
			for row in rows:
				counters["scanned"] += 1
				try:
					_backfill_job(spec, row, counters)
				except Exception:
					# Abort the patch transaction rather than silently stranding a row.
					frappe.log_error(
						frappe.get_traceback(),
						f"Transactional outbox backfill failed for {spec.doctype}",
					)
					raise
			cursor = _clean(_row_value(rows[-1], "name"), 140)
	frappe.logger("aos.outbox", allow_site=True).info(
		"Transactional outbox legacy backfill complete: scanned=%s created=%s existing=%s skipped=%s malformed_payload=%s missing_table=%s",
		counters["scanned"],
		counters["created"],
		counters["existing"],
		counters["skipped_terminal"],
		counters["malformed_payload"],
		counters["missing_table"],
	)
	return counters
