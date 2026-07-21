"""Normalize legacy active outbox rows for durable work/callback reconciliation."""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.services.transactional_outbox import OUTBOX_DOCTYPE, stable_idempotency_key


def execute(batch_size: int = 500) -> dict[str, int]:
	"""Run after model sync in the normal patch transaction without committing."""
	if not frappe.db.table_exists(OUTBOX_DOCTYPE):
		return {"scanned": 0, "normalized": 0}
	batch_size = max(50, min(int(batch_size or 500), 2000))
	cursor = ""
	counters = {"scanned": 0, "normalized": 0}
	while True:
		rows = frappe.db.sql(
			f"""
			SELECT name, status, service_type, job_doctype, job_name, pending_dispatch_reason,
			       attempt_count, max_attempts, dispatch_generation, current_dispatch_token,
			       proposed_dispatch_generation, proposed_dispatch_token
			FROM `tab{OUTBOX_DOCTYPE}`
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
			updates: dict[str, object] = {}
			if str(row.get("pending_dispatch_reason") or "") == "legacy_processing_reconciliation":
				job = frappe.get_doc(row.get("job_doctype"), row.get("job_name"))
				legacy_service_job_id = str(getattr(job, "service_job_id", None) or "").strip()[:200]
				current_key = str(getattr(job, "idempotency_key", None) or "").strip()[:200]
				generated_key = stable_idempotency_key(
					str(row.get("service_type") or ""), row.get("job_doctype"), row.get("job_name")
				)
				if legacy_service_job_id and (not current_key or current_key == generated_key):
					frappe.db.set_value(
						row.get("job_doctype"),
						row.get("job_name"),
						"idempotency_key",
						legacy_service_job_id,
						update_modified=False,
					)
			attempts = max(0, int(row.get("attempt_count") or 0))
			maximum = max(1, int(row.get("max_attempts") or 5))
			if row.get("status") == "Published" and (
				int(row.get("dispatch_generation") or 0) <= 0
				or not str(row.get("current_dispatch_token") or "").strip()
			):
				updates.update(
					{
						"status": "Reconciliation Pending",
						"pending_dispatch_reason": "legacy_processing_reconciliation",
						"next_attempt_at": now_datetime(),
						"callback_deadline_at": None,
						"max_attempts": max(maximum, attempts + 3),
						"last_error": "LEGACY_CALLBACK_CORRELATION_MISSING",
					}
				)
			if row.get("status") in {"Dispatch Uncertain", "Reconciliation Pending"}:
				updates["max_attempts"] = max(maximum, attempts + 1)
			if int(row.get("proposed_dispatch_generation") or 0) > 0 and not str(
				row.get("proposed_dispatch_token") or ""
			).strip():
				updates["proposed_dispatch_generation"] = 0
			if updates:
				frappe.db.set_value(OUTBOX_DOCTYPE, row.name, updates, update_modified=False)
				counters["normalized"] += 1
		cursor = rows[-1]["name"]
	frappe.logger("aos.outbox", allow_site=True).info(
		"Worker/callback lifecycle normalization complete: scanned=%s normalized=%s",
		counters["scanned"],
		counters["normalized"],
	)
	return counters
