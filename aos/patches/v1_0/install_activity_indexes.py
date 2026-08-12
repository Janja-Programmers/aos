"""Install Activity Center uniqueness and timeline indexes."""

from __future__ import annotations

from collections.abc import Sequence

import frappe

INDEX_DEFINITIONS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("uq_aos_activity_active", ("active_key",), True),
    ("idx_aos_activity_user_timeline", ("user", "status", "last_occurrence_at", "creation", "name"), False),
    ("idx_aos_activity_group_timeline", ("user", "status", "activity_group", "last_occurrence_at", "creation", "name"), False),
    ("idx_aos_activity_type_timeline", ("user", "status", "activity_type", "last_occurrence_at", "creation", "name"), False),
    ("idx_aos_activity_route_target", ("route_type", "route_id", "status", "user"), False),
)


def execute() -> None:
    doctype = "AOS User Activity"
    if not frappe.db.table_exists(doctype):
        return
    for index_name, columns, unique in INDEX_DEFINITIONS:
        _ensure_index(doctype, index_name, columns, unique=unique)


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
        frappe.throw(f"Activity schema columns are missing: {', '.join(missing)}")


def _assert_unique_ready(doctype: str, columns: Sequence[str], index_name: str) -> None:
    non_null = " AND ".join(f"`{column}` IS NOT NULL" for column in columns)
    group_by = ", ".join(f"`{column}`" for column in columns)
    duplicate = frappe.db.sql(
        f"""
        SELECT 1 FROM `tab{doctype}`
        WHERE {non_null}
        GROUP BY {group_by}
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    if duplicate:
        frappe.throw(f"Cannot install Activity unique index {index_name}; duplicate active rows remain")


def _ensure_index(doctype: str, index_name: str, columns: tuple[str, ...], *, unique: bool) -> None:
    _ensure_columns(doctype, columns)
    if _index_exists(doctype, index_name):
        return
    if unique:
        _assert_unique_ready(doctype, columns, index_name)
        frappe.db.add_unique(doctype, list(columns), constraint_name=index_name)
    else:
        frappe.db.add_index(doctype, list(columns), index_name=index_name)
