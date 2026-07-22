"""Normalize terminal failures, publisher leases, and bounded reconciliation state."""

from __future__ import annotations

import frappe
from frappe.utils import get_datetime, now_datetime

OUTBOX_DOCTYPE = "AOS Transactional Outbox"


def execute(batch_size: int = 500) -> dict[str, int]:
    """Run after model sync in the normal patch transaction; never commit here."""
    if not frappe.db.table_exists(OUTBOX_DOCTYPE):
        return {"scanned": 0, "normalized": 0}
    size = max(50, min(int(batch_size or 500), 2000))
    cursor = ""
    counters = {"scanned": 0, "normalized": 0}
    normalization_started_at = now_datetime()
    while True:
        rows = frappe.db.sql(
            f"""
            SELECT name, status, callback_status, callback_received_at,
                   attempt_count, max_attempts, reconciliation_attempt_count,
                   reconciliation_max_attempts, dispatch_generation,
                   companion_authoritative_generation, claimed_by, claim_token,
                   lease_expires_at
            FROM `tab{OUTBOX_DOCTYPE}`
            WHERE name > %s
            ORDER BY name ASC
            LIMIT %s
            """,
            (cursor, size),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            counters["scanned"] += 1
            lease_expires_at = row.get("lease_expires_at")
            has_live_lease = bool(
                row.get("claim_token")
                and lease_expires_at
                and get_datetime(lease_expires_at) >= normalization_started_at
            )
            if has_live_lease:
                # Never rewrite a row currently owned by a publisher. The
                # active lease holder remains authoritative; normal stale-lease
                # recovery can handle the row after the lease expires.
                continue

            updates: dict[str, object] = {}
            status = str(row.get("status") or "")
            callback_status = str(row.get("callback_status") or "").lower()
            if status == "Failed" and callback_status == "failed" and row.get("callback_received_at"):
                updates.update(
                    {
                        "status": "Completed With Failure",
                        "completed_at": row.get("callback_received_at") or now_datetime(),
                        "next_attempt_at": None,
                        "claimed_by": None,
                        "claim_token": None,
                        "claimed_at": None,
                        "lease_expires_at": None,
                    }
                )
            if status in {"Claimed", "Dispatched"}:
                updates["status"] = "Queued"
            maximum = max(1, int(row.get("reconciliation_max_attempts") or 10))
            recon = max(0, int(row.get("reconciliation_attempt_count") or 0))
            attempt_count = max(0, int(row.get("attempt_count") or 0))
            max_attempts = max(1, int(row.get("max_attempts") or 5))
            if status == "Dispatch Uncertain" and attempt_count >= max_attempts:
                updates.update(
                    {
                        "status": "Manual Review",
                        "manual_review_reason": "LEGACY_DISPATCH_UNCERTAINTY_EXHAUSTED",
                        "completed_at": now_datetime(),
                        "next_attempt_at": None,
                        "claimed_by": None,
                        "claim_token": None,
                        "claimed_at": None,
                        "lease_expires_at": None,
                    }
                )
            if status == "Reconciliation Pending" and recon >= maximum:
                updates.update(
                    {
                        "status": "Manual Review",
                        "manual_review_reason": "LEGACY_RECONCILIATION_ATTEMPTS_EXHAUSTED",
                        "completed_at": now_datetime(),
                        "next_attempt_at": None,
                    }
                )
            companion_generation = max(0, int(row.get("companion_authoritative_generation") or 0))
            dispatch_generation = max(0, int(row.get("dispatch_generation") or 0))
            if companion_generation < dispatch_generation:
                updates["companion_authoritative_generation"] = dispatch_generation
            if not row.get("reconciliation_max_attempts"):
                updates["reconciliation_max_attempts"] = 10
            if updates:
                frappe.db.set_value(OUTBOX_DOCTYPE, row.name, updates, update_modified=False)
                counters["normalized"] += 1
        cursor = rows[-1]["name"]
    frappe.logger("aos.outbox", allow_site=True).info(
        "Final outbox lifecycle normalization complete: scanned=%s normalized=%s",
        counters["scanned"],
        counters["normalized"],
    )
    return counters
