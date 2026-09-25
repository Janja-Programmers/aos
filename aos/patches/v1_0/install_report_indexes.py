"""Install final Reports indexes after Reports data reconciliation."""

from __future__ import annotations

from collections.abc import Sequence

import frappe

# The old Ad invariant permanently prevented a reporter from submitting another
# report after a terminal outcome. Final Reports uses a Reviewing-only active
# key consistently across User, Ad, and Short reports.
_OBSOLETE_INDEXES: tuple[tuple[str, str], ...] = (
    ("AOS Ad Report", "uq_aos_ad_report_user_ad"),
    ("AOS Report Reason", "idx_aos_report_reason_active"),
)

INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("AOS User Report", "idx_aos_user_report_backlog", ("status", "creation", "name"), False),
    ("AOS User Report", "idx_aos_user_report_target", ("reported_user", "status", "creation", "name"), False),
    ("AOS User Report", "idx_aos_user_report_reporter", ("reported_by", "creation", "name"), False),
    ("AOS User Report", "uq_aos_user_report_active", ("active_key",), True),
    ("AOS Ad Report", "idx_aos_report_backlog", ("status", "creation", "name"), False),
    ("AOS Ad Report", "idx_aos_report_ad_status", ("ad", "status", "creation", "name"), False),
    ("AOS Ad Report", "idx_aos_report_ad_reporter", ("reported_by", "creation", "name"), False),
    ("AOS Ad Report", "uq_aos_ad_report_active", ("active_key",), True),
    ("AOS Short Report", "idx_short_report_review", ("status", "creation", "name"), False),
    ("AOS Short Report", "idx_short_report_target", ("short", "status", "creation", "name"), False),
    ("AOS Short Report", "idx_short_report_reporter", ("reported_by", "creation", "name"), False),
    ("AOS Short Report", "uq_short_report_active", ("active_key",), True),
    ("AOS Report Reason", "idx_aos_report_reason_enabled", ("is_enabled", "sort_order", "label"), False),
    ("AOS Report Reason Target", "idx_aos_report_reason_target_scope", ("target_type", "parent"), False),
    # Reviews is a protected feature; keep its existing report indexes intact.
    ("AOS Review Report", "idx_aos_review_report_backlog", ("status", "creation", "name"), False),
    ("AOS Review Report", "idx_aos_review_report_target", ("review", "status", "creation", "name"), False),
    ("AOS Review Report", "uq_aos_review_report_user", ("review", "reported_by"), True),
)


def execute() -> None:
    for doctype, index_name in _OBSOLETE_INDEXES:
        _drop_index_if_exists(doctype, index_name)
    for doctype, index_name, columns, unique in INDEX_DEFINITIONS:
        _ensure_index(doctype, index_name, columns, unique=unique)


def _table(doctype: str) -> str:
    return f"tab{doctype}"


def _index_exists(doctype: str, index_name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (_table(doctype), index_name),
        )
    )


def _drop_index_if_exists(doctype: str, index_name: str) -> None:
    if not frappe.db.table_exists(doctype) or not _index_exists(doctype, index_name):
        return
    # Names are source-controlled constants above; no client input reaches DDL.
    frappe.db.sql_ddl(f"ALTER TABLE `{_table(doctype)}` DROP INDEX `{index_name}`")


def _ensure_columns(doctype: str, columns: Sequence[str]) -> None:
    if not frappe.db.table_exists(doctype):
        frappe.throw(f"Report schema table is missing: {doctype}")
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(f"Report schema columns are missing for {doctype}: {', '.join(missing)}")


def _assert_unique_ready(doctype: str, columns: Sequence[str], index_name: str) -> None:
    non_null = " AND ".join(f"`{column}` IS NOT NULL" for column in columns)
    group_by = ", ".join(f"`{column}`" for column in columns)
    duplicate = frappe.db.sql(
        f"""
        SELECT 1
        FROM `{_table(doctype)}`
        WHERE {non_null}
        GROUP BY {group_by}
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    if duplicate:
        frappe.throw(
            f"Cannot install Report unique index {index_name}; duplicate data remains in {doctype}"
        )


def _ensure_index(
    doctype: str,
    index_name: str,
    columns: tuple[str, ...],
    *,
    unique: bool,
) -> None:
    _ensure_columns(doctype, columns)
    if _index_exists(doctype, index_name):
        return
    if unique:
        _assert_unique_ready(doctype, columns, index_name)

    fields = list(columns)
    if unique:
        frappe.db.add_unique(doctype, fields, constraint_name=index_name)
    else:
        frappe.db.add_index(doctype, fields, index_name=index_name)
