"""Install query indexes used by the production Notification subsystem."""

from __future__ import annotations

from collections.abc import Sequence

import frappe

INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("AOS Notification", "idx_aos_notification_user_timeline", ("user", "creation", "name")),
    ("AOS Notification", "idx_aos_notification_category_timeline", ("user", "type", "creation", "name")),
    ("AOS Notification", "idx_aos_notification_unread", ("user", "is_read", "creation")),
    ("AOS Push Token", "idx_aos_push_token_user_active", ("user", "is_active")),
    ("AOS Push Token", "idx_aos_push_token_user_device", ("user", "device_id")),
    ("AOS Notification Delivery Job", "idx_aos_notification_job_user_status", ("user", "status")),
    (
        "AOS Notification Delivery Job",
        "idx_aos_notification_job_retry",
        ("status", "attempt_count", "creation"),
    ),
)


def execute() -> None:
    for doctype, index_name, columns in INDEX_DEFINITIONS:
        if not frappe.db.table_exists(doctype):
            continue
        _ensure_columns(doctype, columns)
        if not _index_exists(doctype, index_name):
            frappe.db.add_index(doctype, list(columns), index_name=index_name)


def _index_exists(doctype: str, index_name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1 FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s
            LIMIT 1
            """,
            (f"tab{doctype}", index_name),
        )
    )


def _ensure_columns(doctype: str, columns: Sequence[str]) -> None:
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(f"Notification schema columns are missing: {', '.join(missing)}")
