"""Normalize terminal failures, publisher leases, and bounded reconciliation state."""

from __future__ import annotations

import time
from collections.abc import Sequence

import frappe
from frappe.utils import get_datetime, now_datetime

OUTBOX_DOCTYPE = "AOS Transactional Outbox"
_LOCK_RETRY_LIMIT = 8


def _normalize_names(names: Sequence[str] | None) -> tuple[str, ...]:
    if names is None:
        return ()
    normalized = tuple(dict.fromkeys(str(name or "").strip() for name in names if str(name or "").strip()))
    if len(normalized) > 2000:
        raise ValueError("Outbox normalization is limited to 2000 explicit names per execution.")
    return normalized


def _candidate_where(*, names: tuple[str, ...]) -> tuple[str, tuple[object, ...]]:
    clauses = [
        """
        (
            (status = 'Failed' AND LOWER(COALESCE(callback_status, '')) = 'failed'
             AND callback_received_at IS NOT NULL)
            OR status IN ('Claimed', 'Dispatched')
            OR (status = 'Dispatch Uncertain'
                AND COALESCE(attempt_count, 0) >= GREATEST(1, COALESCE(max_attempts, 5)))
            OR (status = 'Reconciliation Pending'
                AND COALESCE(reconciliation_attempt_count, 0)
                    >= GREATEST(1, COALESCE(reconciliation_max_attempts, 10)))
            OR COALESCE(companion_authoritative_generation, 0)
                < COALESCE(dispatch_generation, 0)
            OR COALESCE(reconciliation_max_attempts, 0) = 0
        )
        """
    ]
    params: list[object] = []
    if names:
        clauses.append(f"name IN ({', '.join(['%s'] * len(names))})")
        params.extend(names)
    return " AND ".join(f"({clause.strip()})" for clause in clauses), tuple(params)


def _remaining_candidates(
    *,
    where_sql: str,
    where_params: tuple[object, ...],
    normalization_started_at,
) -> int:
    result = frappe.db.sql(
        f"""
        SELECT COUNT(*)
        FROM `tab{OUTBOX_DOCTYPE}`
        WHERE {where_sql}
          AND NOT (
              claim_token IS NOT NULL
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at >= %s
          )
        """,
        (*where_params, normalization_started_at),
    )
    return int(result[0][0] or 0) if result else 0


def execute(batch_size: int = 500, names: Sequence[str] | None = None) -> dict[str, int]:
    """Run after model sync in the normal patch transaction; never commit here.

    ``names`` is an internal bounded scope used by deterministic tests and
    operational repair tooling. Normal patch execution leaves it unset and
    processes every eligible legacy row.
    """
    if not frappe.db.table_exists(OUTBOX_DOCTYPE):
        return {"scanned": 0, "normalized": 0}

    size = max(50, min(int(batch_size or 500), 2000))
    scoped_names = _normalize_names(names)
    if names is not None and not scoped_names:
        return {"scanned": 0, "normalized": 0}

    where_sql, where_params = _candidate_where(names=scoped_names)
    counters = {"scanned": 0, "normalized": 0}
    normalization_started_at = now_datetime()
    empty_attempts = 0

    while True:
        rows = frappe.db.sql(
            f"""
            SELECT name, status, callback_status, callback_received_at,
                   attempt_count, max_attempts, reconciliation_attempt_count,
                   reconciliation_max_attempts, dispatch_generation,
                   companion_authoritative_generation, claimed_by, claim_token,
                   lease_expires_at
            FROM `tab{OUTBOX_DOCTYPE}`
            WHERE {where_sql}
              AND NOT (
                  claim_token IS NOT NULL
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at >= %s
              )
            ORDER BY name ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (*where_params, normalization_started_at, size),
            as_dict=True,
        )
        if not rows:
            remaining = _remaining_candidates(
                where_sql=where_sql,
                where_params=where_params,
                normalization_started_at=normalization_started_at,
            )
            if not remaining:
                break
            empty_attempts += 1
            if empty_attempts >= _LOCK_RETRY_LIMIT:
                raise RuntimeError(
                    "Outbox lifecycle normalization could not acquire all candidate rows; retry migration."
                )
            time.sleep(min(0.05 * (2 ** (empty_attempts - 1)), 1.0))
            continue

        empty_attempts = 0
        for row in rows:
            counters["scanned"] += 1
            lease_expires_at = row.get("lease_expires_at")
            has_live_lease = bool(
                row.get("claim_token")
                and lease_expires_at
                and get_datetime(lease_expires_at) >= normalization_started_at
            )
            if has_live_lease:
                # The SQL predicate already excludes these rows. Keep the
                # defensive check so a row whose lease changes unexpectedly is
                # never rewritten by this migration.
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
                frappe.db.set_value(OUTBOX_DOCTYPE, row["name"], updates, update_modified=False)
                counters["normalized"] += 1

    frappe.logger("aos.outbox", allow_site=True).info(
        "Final outbox lifecycle normalization complete: scanned=%s normalized=%s",
        counters["scanned"],
        counters["normalized"],
    )
    return counters
