"""Install and repair Report indexes in a schema-only patch.

The shared Report data-hardening patch runs after Frappe model sync and must not
mix data reconciliation with MariaDB DDL. Older Report hardening revisions also
force-reloaded Report DocTypes after Ads, Shorts, and Reviews had installed
manual indexes; that could remove domain-owned constraints such as
``uq_short_report_active``. This idempotent schema-only patch installs the new
User/Reason indexes and restores all existing domain-owned report indexes.
"""

from __future__ import annotations

from collections.abc import Sequence

import frappe


INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    # Shared/User Report hardening indexes.
    ("AOS User Report", "idx_aos_user_report_backlog", ("status", "creation", "name"), False),
    (
        "AOS User Report",
        "idx_aos_user_report_target",
        ("reported_user", "status", "creation", "name"),
        False,
    ),
    (
        "AOS User Report",
        "idx_aos_user_report_reporter",
        ("reported_by", "creation", "name"),
        False,
    ),
    ("AOS User Report", "uq_aos_user_report_active", ("active_key",), True),
    (
        "AOS Report Reason",
        "idx_aos_report_reason_active",
        ("is_active", "sort_order", "title"),
        False,
    ),
    # Ads-owned report indexes.
    ("AOS Ad Report", "idx_aos_report_backlog", ("status", "creation", "name"), False),
    ("AOS Ad Report", "idx_aos_report_ad_status", ("ad", "status", "creation"), False),
    ("AOS Ad Report", "uq_aos_ad_report_user_ad", ("ad", "reported_by"), True),
    # Shorts-owned report indexes.
    ("AOS Short Report", "idx_short_report_review", ("status", "creation", "name"), False),
    ("AOS Short Report", "uq_short_report_active", ("active_key",), True),
    # Reviews-owned report indexes.
    (
        "AOS Review Report",
        "idx_aos_review_report_backlog",
        ("status", "creation", "name"),
        False,
    ),
    (
        "AOS Review Report",
        "idx_aos_review_report_target",
        ("review", "status", "creation", "name"),
        False,
    ),
    ("AOS Review Report", "uq_aos_review_report_user", ("review", "reported_by"), True),
)


def execute() -> None:
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
