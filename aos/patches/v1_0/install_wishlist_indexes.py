"""Schema-only Wishlist indexes for the current fresh-site architecture."""

from __future__ import annotations

from collections.abc import Sequence

import frappe

INDEXES: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("uq_aos_wishlist_user_ad", ("user", "ad"), True),
    ("idx_aos_wishlist_user_status_saved", ("user", "status", "saved_on", "name"), False),
    ("idx_aos_wishlist_ad_status", ("ad", "status"), False),
)


def execute() -> None:
    if not frappe.db.table_exists("AOS Wishlist"):
        frappe.throw("Wishlist schema table is missing: AOS Wishlist")
    for name, columns, unique in INDEXES:
        _ensure_index(name, columns, unique=unique)


def _ensure_columns(columns: Sequence[str]) -> None:
    missing = [column for column in columns if not frappe.db.has_column("AOS Wishlist", column)]
    if missing:
        frappe.throw(f"Wishlist schema columns are missing: {', '.join(missing)}")


def _index_definition(name: str) -> tuple[tuple[str, ...], bool] | None:
    rows = frappe.db.sql(
        """
        SELECT COLUMN_NAME, NON_UNIQUE
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'tabAOS Wishlist'
          AND INDEX_NAME = %s
        ORDER BY SEQ_IN_INDEX
        """,
        (name,),
        as_dict=True,
    )
    if not rows:
        return None
    return tuple(str(row["COLUMN_NAME"]) for row in rows), not bool(int(rows[0]["NON_UNIQUE"]))


def _assert_unique_ready(columns: Sequence[str], name: str) -> None:
    predicates = " AND ".join(f"`{column}` IS NOT NULL AND `{column}` != ''" for column in columns)
    groups = ", ".join(f"`{column}`" for column in columns)
    duplicates = frappe.db.sql(
        f"""
        SELECT 1
        FROM `tabAOS Wishlist`
        WHERE {predicates}
        GROUP BY {groups}
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    if duplicates:
        frappe.throw(f"Cannot install Wishlist unique index {name}; duplicate relationship rows exist")


def _drop_index(name: str) -> None:
    db_type = str(getattr(frappe.db, "db_type", "mariadb") or "mariadb").lower()
    if db_type == "postgres":
        frappe.db.sql_ddl(f'DROP INDEX IF EXISTS "{name}"')
    else:
        frappe.db.sql_ddl(f"ALTER TABLE `tabAOS Wishlist` DROP INDEX `{name}`")


def _ensure_index(name: str, columns: tuple[str, ...], *, unique: bool) -> None:
    _ensure_columns(columns)
    expected = (columns, unique)
    existing = _index_definition(name)
    if existing == expected:
        return
    if unique:
        _assert_unique_ready(columns, name)
    if existing is not None:
        _drop_index(name)
    if unique:
        frappe.db.add_unique("AOS Wishlist", list(columns), constraint_name=name)
    else:
        frappe.db.add_index("AOS Wishlist", list(columns), index_name=name)
