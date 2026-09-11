"""Bounded retention for Notifications inbox and delivery operational records."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_days, now_datetime

from aos.utils.aos_config import get_env_bool, get_env_int

_SUCCESS_JOB_STATUSES = ("Delivered", "Skipped")
_FAILURE_JOB_STATUSES = ("Failed", "Cancelled")
_TERMINAL_OUTBOX_STATUSES = (
    "Completed",
    "Completed With Failure",
    "Manual Review",
    "Dead Letter",
    "Cancelled",
)


def get_notification_retention_config() -> dict[str, int | bool]:
    return {
        "enabled": get_env_bool("AOS_NOTIFICATION_RETENTION_ENABLED", True),
        "inbox_days": get_env_int("AOS_NOTIFICATION_INBOX_RETENTION_DAYS", 180, min_value=30, max_value=3650),
        "delivery_success_days": get_env_int("AOS_NOTIFICATION_DELIVERY_SUCCESS_RETENTION_DAYS", 30, min_value=7, max_value=3650),
        "delivery_failure_days": get_env_int("AOS_NOTIFICATION_DELIVERY_FAILURE_RETENTION_DAYS", 90, min_value=30, max_value=3650),
        "batch_size": get_env_int("AOS_NOTIFICATION_RETENTION_BATCH_SIZE", 1000, min_value=100, max_value=5000),
        "max_batches": get_env_int("AOS_NOTIFICATION_RETENTION_MAX_BATCHES", 20, min_value=1, max_value=100),
    }


def cleanup_notification_retention(*, dry_run: bool = False) -> dict[str, Any]:
    """Delete expired Notification data in bounded, index-backed batches.

    The Notification Center is a user-facing recent-event inbox, not a permanent
    audit ledger. Delivery records are operational diagnostics and are kept long
    enough for retry/reconciliation before deletion. Nonterminal work is never
    deleted.
    """
    config = get_notification_retention_config()
    if not bool(config["enabled"]):
        return {"ok": True, "enabled": False, "dry_run": dry_run, "deleted": {}}

    size = int(config["batch_size"])
    max_batches = int(config["max_batches"])
    now = now_datetime()
    deleted = {
        "inbox": _delete_inbox(
            cutoff=add_days(now, -int(config["inbox_days"])),
            batch_size=size,
            max_batches=max_batches,
            dry_run=dry_run,
        ),
        "delivery_success": _delete_delivery_jobs(
            statuses=_SUCCESS_JOB_STATUSES,
            cutoff=add_days(now, -int(config["delivery_success_days"])),
            batch_size=size,
            max_batches=max_batches,
            dry_run=dry_run,
        ),
        "delivery_failure": _delete_delivery_jobs(
            statuses=_FAILURE_JOB_STATUSES,
            cutoff=add_days(now, -int(config["delivery_failure_days"])),
            batch_size=size,
            max_batches=max_batches,
            dry_run=dry_run,
        ),
    }
    if not dry_run:
        frappe.db.commit()
    return {"ok": True, "enabled": True, "dry_run": dry_run, "config": config, "deleted": deleted}


def _delete_inbox(*, cutoff, batch_size: int, max_batches: int, dry_run: bool) -> int:
    if not frappe.db.table_exists("AOS Notification"):
        return 0
    total = 0
    for _ in range(max_batches):
        rows = frappe.db.sql(
            """
            SELECT n.name
            FROM `tabAOS Notification` n
            WHERE n.creation < %s
              AND NOT EXISTS (
                SELECT 1
                FROM `tabAOS Notification Delivery Job` j
                WHERE j.notification = n.name
                  AND j.status NOT IN ('Delivered', 'Skipped', 'Failed', 'Cancelled')
              )
            ORDER BY n.creation, n.name
            LIMIT %s
            """,
            (cutoff, batch_size),
            as_dict=True,
        )
        names = tuple(str(row.name) for row in rows if row.name)
        if not names:
            break
        total += len(names)
        if dry_run:
            break
        frappe.db.sql("DELETE FROM `tabAOS Notification` WHERE name IN %(names)s", {"names": names})
        if len(names) < batch_size:
            break
    return total


def _delete_delivery_jobs(
    *, statuses: tuple[str, ...], cutoff, batch_size: int, max_batches: int, dry_run: bool
) -> int:
    if not frappe.db.table_exists("AOS Notification Delivery Job"):
        return 0
    total = 0
    outbox_exists = frappe.db.table_exists("AOS Transactional Outbox")
    for _ in range(max_batches):
        outbox_clause = ""
        params: dict[str, Any] = {"statuses": statuses, "cutoff": cutoff, "limit": batch_size}
        if outbox_exists:
            outbox_clause = """
              AND NOT EXISTS (
                SELECT 1 FROM `tabAOS Transactional Outbox` o
                WHERE o.job_doctype = 'AOS Notification Delivery Job'
                  AND o.job_name = j.name
                  AND o.status NOT IN %(terminal_outbox)s
              )
            """
            params["terminal_outbox"] = _TERMINAL_OUTBOX_STATUSES
        rows = frappe.db.sql(
            f"""
            SELECT j.name
            FROM `tabAOS Notification Delivery Job` j
            WHERE j.status IN %(statuses)s
              AND j.completed_at IS NOT NULL
              AND j.completed_at < %(cutoff)s
              {outbox_clause}
            ORDER BY j.completed_at, j.name
            LIMIT %(limit)s
            """,
            params,
            as_dict=True,
        )
        names = tuple(str(row.name) for row in rows if row.name)
        if not names:
            break
        total += len(names)
        if dry_run:
            break
        if outbox_exists:
            frappe.db.sql(
                """
                DELETE FROM `tabAOS Transactional Outbox`
                WHERE job_doctype = 'AOS Notification Delivery Job'
                  AND job_name IN %(names)s
                  AND status IN %(terminal)s
                """,
                {"names": names, "terminal": _TERMINAL_OUTBOX_STATUSES},
            )
        frappe.db.sql(
            "DELETE FROM `tabAOS Notification Delivery Job` WHERE name IN %(names)s",
            {"names": names},
        )
        if len(names) < batch_size:
            break
    return total
