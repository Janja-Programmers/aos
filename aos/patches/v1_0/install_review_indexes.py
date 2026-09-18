"""Schema-only Reviews indexes for the current fresh-site architecture."""

from __future__ import annotations

from collections.abc import Sequence

import frappe

INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("AOS Review", "uq_aos_review_key", ("review_key",), True),
    ("AOS Review", "idx_aos_review_public_ad", ("ad", "status", "creation", "public_id"), False),
    ("AOS Review", "idx_aos_review_public_helpful", ("ad", "status", "like_count", "creation", "public_id"), False),
    ("AOS Review", "idx_aos_review_public_rating", ("ad", "status", "rating", "creation", "public_id"), False),
    ("AOS Review", "idx_aos_review_reviewer_history", ("reviewer", "creation", "public_id"), False),
    ("AOS Review", "idx_aos_review_moderation", ("status", "creation", "public_id"), False),
    ("AOS Review Reaction", "uq_aos_review_reaction_user", ("review", "user"), True),
)


def execute() -> None:
    for doctype, index_name, columns, unique in INDEX_DEFINITIONS:
        _ensure_index(doctype, index_name, columns, unique=unique)


def _ensure_columns(doctype: str, columns: Sequence[str]) -> None:
    if not frappe.db.table_exists(doctype):
        frappe.throw(f"Reviews schema table is missing: {doctype}")
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(f"Reviews schema columns are missing on {doctype}: {', '.join(missing)}")


def _definition(doctype: str, name: str) -> tuple[tuple[str, ...], bool] | None:
    rows = frappe.db.sql(
        """
        SELECT COLUMN_NAME, NON_UNIQUE
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s
        ORDER BY SEQ_IN_INDEX
        """,
        (f"tab{doctype}", name),
        as_dict=True,
    )
    if not rows:
        return None
    return tuple(str(row.COLUMN_NAME) for row in rows), not bool(int(rows[0].NON_UNIQUE))


def _assert_unique_ready(doctype: str, columns: Sequence[str], name: str) -> None:
    predicates = " AND ".join(f"`{column}` IS NOT NULL AND `{column}` != ''" for column in columns)
    groups = ", ".join(f"`{column}`" for column in columns)
    duplicates = frappe.db.sql(
        f"SELECT 1 FROM `tab{doctype}` WHERE {predicates} GROUP BY {groups} HAVING COUNT(*) > 1 LIMIT 1"
    )
    if duplicates:
        frappe.throw(f"Cannot install Reviews unique index {name}; duplicate domain rows exist")


def _drop_index(doctype: str, name: str) -> None:
    if str(getattr(frappe.db, "db_type", "mariadb") or "mariadb").lower() == "postgres":
        frappe.db.sql_ddl(f'DROP INDEX IF EXISTS "{name}"')
    else:
        frappe.db.sql_ddl(f"ALTER TABLE `tab{doctype}` DROP INDEX `{name}`")


def _ensure_index(doctype: str, name: str, columns: tuple[str, ...], *, unique: bool) -> None:
    _ensure_columns(doctype, columns)
    expected = (columns, unique)
    existing = _definition(doctype, name)
    if existing == expected:
        return
    if unique:
        _assert_unique_ready(doctype, columns, name)
    if existing is not None:
        _drop_index(doctype, name)
    if unique:
        frappe.db.add_unique(doctype, list(columns), constraint_name=name)
    else:
        frappe.db.add_index(doctype, list(columns), index_name=name)
