"""Low-cardinality Ads operational logging helpers."""

from __future__ import annotations

from typing import Any

import frappe


def ads_log(
    event: str,
    *,
    status: Any = None,
    operation: Any = None,
    outcome: Any = None,
    count: int | None = None,
) -> None:
    """Emit a safe structured log without IDs, titles, users, or locations."""

    payload = {
        "event": str(event or "ads_event")[:64],
        "status": str(status or "")[:32],
        "operation": str(operation or "")[:32],
        "outcome": str(outcome or "")[:32],
        "count": max(0, int(count or 0)),
    }
    try:
        frappe.logger("aos.ads", allow_site=True).info("ads_event %s", payload)
    except Exception:
        pass


def ads_operational_snapshot() -> dict[str, Any]:
    """Return a redacted, low-cardinality Ads operations snapshot.

    The report intentionally contains counts and bounded age values only. It is
    suitable for Bench diagnostics and does not expose Ad, seller, or user IDs.
    """

    if not frappe.db.table_exists("AOS Ad"):
        return {
            "available": False,
            "counts_by_status": {},
            "overdue_active_count": 0,
            "oldest_overdue_seconds": 0,
            "reviewing_count": 0,
            "oldest_reviewing_seconds": 0,
            "failed_ad_moderation_jobs": 0,
            "active_ad_moderation_jobs": 0,
        }

    status_rows = frappe.db.sql(
        """
        SELECT status, COUNT(*) AS count
        FROM `tabAOS Ad`
        GROUP BY status
        ORDER BY status
        """,
        as_dict=True,
    )
    counts_by_status = {
        str(row.status or "unknown")[:32]: max(0, int(row.count or 0))
        for row in status_rows
    }
    overdue = frappe.db.sql(
        """
        SELECT COUNT(*) AS count,
               COALESCE(MAX(TIMESTAMPDIFF(SECOND, expires_on, NOW())), 0) AS oldest_seconds
        FROM `tabAOS Ad`
        WHERE status = 'Active'
          AND expires_on IS NOT NULL
          AND expires_on < CURRENT_DATE()
        """,
        as_dict=True,
    )[0]
    reviewing = frappe.db.sql(
        """
        SELECT COUNT(*) AS count,
               COALESCE(MAX(TIMESTAMPDIFF(SECOND, creation, NOW())), 0) AS oldest_seconds
        FROM `tabAOS Ad`
        WHERE status = 'Reviewing'
        """,
        as_dict=True,
    )[0]

    moderation = {"active": 0, "failed": 0}
    if frappe.db.table_exists("AOS Moderation Job"):
        rows = frappe.db.sql(
            """
            SELECT
                SUM(CASE WHEN status IN ('Queued', 'Dispatching', 'Processing') THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status = 'Failed' THEN 1 ELSE 0 END) AS failed
            FROM `tabAOS Moderation Job`
            WHERE target_doctype = 'AOS Ad'
            """,
            as_dict=True,
        )
        if rows:
            moderation = {
                "active": max(0, int(rows[0].active or 0)),
                "failed": max(0, int(rows[0].failed or 0)),
            }

    return {
        "available": True,
        "counts_by_status": counts_by_status,
        "overdue_active_count": max(0, int(overdue.count or 0)),
        "oldest_overdue_seconds": max(0, int(overdue.oldest_seconds or 0)),
        "reviewing_count": max(0, int(reviewing.count or 0)),
        "oldest_reviewing_seconds": max(0, int(reviewing.oldest_seconds or 0)),
        "failed_ad_moderation_jobs": moderation["failed"],
        "active_ad_moderation_jobs": moderation["active"],
    }
