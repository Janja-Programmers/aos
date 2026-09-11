"""Idempotent schema-only indexes for the canonical Notifications domain.

Safe on fresh installs and from ``after_migrate``.  The installer verifies the
exact index shape so later DocType synchronization cannot silently weaken hot
query paths or concurrency constraints.
"""

from __future__ import annotations

from collections.abc import Sequence

import frappe

INDEXES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("AOS Notification", "idx_aos_notification_user_timeline", ("user", "creation", "name"), False),
    ("AOS Notification", "idx_aos_notification_category_timeline", ("user", "type", "creation", "name"), False),
    ("AOS Notification", "idx_aos_notification_unread", ("user", "is_read", "creation"), False),
    ("AOS Notification", "idx_aos_notification_retention", ("creation", "name"), False),
    ("AOS Push Token", "idx_aos_push_token_user_active", ("user", "is_active", "last_used_at", "name"), False),
    ("AOS Push Token", "idx_aos_push_token_user_device", ("user", "device_id", "is_active", "name"), False),
    ("AOS Push Token", "uq_aos_push_token_active_device", ("active_device_key",), True),
    ("AOS Notification Delivery Job", "idx_aos_notification_job_notification_status", ("notification", "status", "name"), False),
    ("AOS Notification Delivery Job", "idx_aos_notification_job_user_status", ("user", "status"), False),
    ("AOS Notification Delivery Job", "idx_aos_notification_job_retry", ("status", "attempt_count", "creation", "name"), False),
    ("AOS Notification Delivery Job", "idx_aos_notification_job_retention", ("status", "completed_at", "name"), False),
)

# Historical tests/importers used this name. It is not a compatibility API; it
# is simply a source-level alias inside the schema installer.
INDEX_DEFINITIONS = tuple((doctype, name, columns) for doctype, name, columns, _ in INDEXES)


def execute() -> None:
    for doctype, name, columns, unique in INDEXES:
        if not frappe.db.table_exists(doctype):
            continue
        _ensure_index(doctype, name, columns, unique=unique)


def _table(doctype: str) -> str:
    return f"tab{doctype}"


def _ensure_columns(doctype: str, columns: Sequence[str]) -> None:
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(f"Notifications schema columns are missing for {doctype}: {', '.join(missing)}")


def _index_definition(doctype: str, name: str) -> tuple[tuple[str, ...], bool] | None:
    rows = frappe.db.sql(
        """
        SELECT COLUMN_NAME, NON_UNIQUE
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
        ORDER BY SEQ_IN_INDEX
        """,
        (_table(doctype), name),
        as_dict=True,
    )
    if not rows:
        return None
    return tuple(str(row["COLUMN_NAME"]) for row in rows), not bool(int(rows[0]["NON_UNIQUE"]))


def _assert_unique_ready(doctype: str, columns: Sequence[str], name: str) -> None:
    non_null = " AND ".join(f"`{column}` IS NOT NULL AND `{column}`!=''" for column in columns)
    groups = ", ".join(f"`{column}`" for column in columns)
    duplicate = frappe.db.sql(
        f"SELECT 1 FROM `{_table(doctype)}` WHERE {non_null} GROUP BY {groups} HAVING COUNT(*)>1 LIMIT 1"
    )
    if duplicate:
        frappe.throw(f"Cannot install Notifications unique index {name}; duplicate data remains in {doctype}")


def _drop_static_index(doctype: str, name: str) -> None:
    db_type = str(getattr(frappe.db, "db_type", "mariadb") or "mariadb").lower()
    if db_type == "postgres":
        frappe.db.sql_ddl(f'DROP INDEX IF EXISTS "{name}"')
    else:
        frappe.db.sql_ddl(f"ALTER TABLE `{_table(doctype)}` DROP INDEX `{name}`")


def _ensure_index(doctype: str, name: str, columns: tuple[str, ...], *, unique: bool) -> None:
    _ensure_columns(doctype, columns)
    expected = (columns, unique)
    existing = _index_definition(doctype, name)
    if existing == expected:
        return
    if unique:
        _assert_unique_ready(doctype, columns, name)
    if existing is not None:
        _drop_static_index(doctype, name)
    if unique:
        frappe.db.add_unique(doctype, list(columns), constraint_name=name)
    else:
        frappe.db.add_index(doctype, list(columns), index_name=name)
