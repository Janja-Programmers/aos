"""Install current Social uniqueness and query indexes.

Schema-only and idempotent: fresh-site DocType synchronization creates the
columns, then this installer asserts the composite indexes Social requires for
concurrency correctness and keyset pagination.
"""

from __future__ import annotations

from collections.abc import Sequence

import frappe

INDEXES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("AOS Follow", "uq_social_follow_pair", ("follower_user", "following_user"), True),
    ("AOS Follow", "idx_social_following_page", ("follower_user", "creation", "name"), False),
    ("AOS Follow", "idx_social_follower_page", ("following_user", "creation", "name"), False),
    ("AOS Follow", "idx_social_follow_reverse", ("following_user", "follower_user"), False),
    ("AOS User Block", "uq_social_block_pair", ("blocker_user", "blocked_user"), True),
    ("AOS User Block", "idx_social_blocker_page", ("blocker_user", "status", "blocked_at", "name"), False),
    ("AOS User Block", "idx_social_blocked_lookup", ("blocked_user", "status", "blocker_user"), False),
    ("AOS Profile", "idx_social_profile_discovery", ("account_status", "display_name", "name"), False),
)


def execute() -> None:
    for doctype, index_name, fields, unique in INDEXES:
        _ensure_index(doctype, index_name, fields, unique=unique)


def _table(doctype: str) -> str:
    return f"tab{doctype}"


def _ensure_columns(doctype: str, fields: Sequence[str]) -> None:
    if not frappe.db.table_exists(doctype):
        frappe.throw(f"Social schema table is missing: {doctype}")
    missing = [field for field in fields if not frappe.db.has_column(doctype, field)]
    if missing:
        frappe.throw(f"Social schema columns are missing for {doctype}: {', '.join(missing)}")


def _assert_unique_ready(doctype: str, fields: Sequence[str], index_name: str) -> None:
    non_null = " AND ".join(f"`{field}` IS NOT NULL AND `{field}`!=''" for field in fields)
    groups = ", ".join(f"`{field}`" for field in fields)
    duplicate = frappe.db.sql(
        f"SELECT 1 FROM `{_table(doctype)}` WHERE {non_null} GROUP BY {groups} HAVING COUNT(*)>1 LIMIT 1"
    )
    if duplicate:
        frappe.throw(f"Cannot install Social unique index {index_name}; duplicate data exists in {doctype}")


def _ensure_index(doctype: str, index_name: str, fields: tuple[str, ...], *, unique: bool) -> None:
    _ensure_columns(doctype, fields)
    existing = _index_definition(doctype, index_name)
    expected = (fields, unique)
    if existing == expected:
        return
    if unique:
        _assert_unique_ready(doctype, fields, index_name)
    if existing is not None:
        _drop_index(doctype, index_name)
    if unique:
        frappe.db.add_unique(doctype, list(fields), constraint_name=index_name)
    else:
        frappe.db.add_index(doctype, list(fields), index_name=index_name)


def _index_definition(doctype: str, index_name: str) -> tuple[tuple[str, ...], bool] | None:
    rows = frappe.db.sql(
        """
        SELECT COLUMN_NAME, NON_UNIQUE
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
        ORDER BY SEQ_IN_INDEX
        """,
        (_table(doctype), index_name),
        as_dict=True,
    )
    if not rows:
        return None
    fields = tuple(str(row.COLUMN_NAME) for row in rows)
    unique = not bool(int(rows[0].NON_UNIQUE or 0))
    return fields, unique


def _drop_index(doctype: str, index_name: str) -> None:
    table_name = _table(doctype)
    db_type = str(getattr(frappe.db, "db_type", "mariadb") or "mariadb").lower()
    if db_type == "postgres":
        frappe.db.sql_ddl(f'DROP INDEX IF EXISTS "{index_name}"')
        return
    frappe.db.sql_ddl(f"ALTER TABLE `{table_name}` DROP INDEX `{index_name}`")
