"""Normalize existing outbox rows for explicit recovery-dispatch metadata."""

from __future__ import annotations

import frappe

OUTBOX_DOCTYPE = "AOS Transactional Outbox"


def execute() -> None:
	if not frappe.db.table_exists(OUTBOX_DOCTYPE):
		return
	frappe.db.sql(
		f"""
		UPDATE `tab{OUTBOX_DOCTYPE}`
		SET pending_dispatch_reason = COALESCE(NULLIF(pending_dispatch_reason, ''), 'initial'),
		    active_dispatch_reason = COALESCE(NULLIF(active_dispatch_reason, ''), 'initial'),
		    completed_dispatch_generation = CASE
		      WHEN status IN ('Completed', 'Completed With Failure', 'Dead Letter', 'Cancelled')
		       AND COALESCE(completed_dispatch_generation, 0) = 0
		      THEN COALESCE(dispatch_generation, 0)
		      ELSE COALESCE(completed_dispatch_generation, 0)
		    END
		"""
	)
