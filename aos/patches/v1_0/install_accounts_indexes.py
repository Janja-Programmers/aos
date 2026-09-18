"""Install Accounts query indexes that are not expressible in DocType metadata.

The patch is schema-only and idempotent. A matching index is verified by both
its name and ordered column list so an incorrect index with the same name cannot
silently satisfy the schema invariant.
"""

from __future__ import annotations

import frappe

INDEXES = {
    "idx_aos_profile_lifecycle": ("AOS Profile", ["account_status", "restore_deadline"]),
    "idx_aos_profile_purge_scan": ("AOS Profile", ["account_status", "purge_status", "restore_deadline"]),
}


def execute() -> None:
    if not frappe.db.table_exists("AOS Profile"):
        return
    for name, (doctype, fields) in INDEXES.items():
        if not all(frappe.db.has_column(doctype, field) for field in fields):
            continue
        _ensure_exact_index(doctype, fields, name)


def _ensure_exact_index(doctype: str, fields: list[str], index_name: str) -> None:
    existing = _index_columns(doctype, index_name)
    expected = tuple(fields)
    if existing == expected:
        return
    if existing is not None:
        _drop_index(doctype, index_name)
    frappe.db.add_index(doctype, fields, index_name=index_name)


def _index_columns(doctype: str, index_name: str) -> tuple[str, ...] | None:
    rows = frappe.db.sql(
        """
        SELECT COLUMN_NAME
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
        ORDER BY SEQ_IN_INDEX
        """,
        (f"tab{doctype}", index_name),
        pluck=True,
    )
    return tuple(str(column) for column in rows) if rows else None


def _drop_index(doctype: str, index_name: str) -> None:
    """Drop a statically-declared Accounts index before recreating it correctly."""
    table_name = f"tab{doctype}"
    db_type = str(getattr(frappe.db, "db_type", "mariadb") or "mariadb").lower()
    if db_type == "postgres":
        frappe.db.sql_ddl(f'DROP INDEX IF EXISTS "{index_name}"')
        return
    frappe.db.sql_ddl(f"ALTER TABLE `{table_name}` DROP INDEX `{index_name}`")
