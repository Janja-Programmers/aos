"""Add bounded Catalog indexes and safe legacy defaults.

The patch is additive, idempotent, and deliberately avoids deleting or
renaming production taxonomy records. Legacy duplicate child rows remain
preserved; runtime resolution is deterministic and new duplicates are rejected
by document validation.
"""

from __future__ import annotations

from typing import Any

import frappe

INDEXES = {
    "idx_aos_category_public_tree": (
        "AOS Category",
        ["is_active", "parent_aos_category", "is_group", "sort_order"],
    ),
    "idx_aos_category_attribute_lookup": (
        "AOS Category Attribute Row",
        ["parent", "parentfield", "is_active", "sort_order"],
    ),
    "idx_aos_attribute_active_label": (
        "AOS Ad Attribute",
        ["is_active", "label"],
    ),
}

_BATCH_SIZE = 500


def execute() -> None:
    for index_name, (doctype, fields) in INDEXES.items():
        if not frappe.db.table_exists(doctype):
            continue
        if any(not frappe.db.has_column(doctype, field) for field in fields):
            continue
        if _index_exists(doctype, index_name):
            continue
        frappe.db.add_index(doctype, fields, index_name=index_name)

    if frappe.db.table_exists("AOS Category"):
        _normalize_in_batches(
            doctype="AOS Category",
            field="pricing_requirement",
            value="Optional",
            where_sql="COALESCE(pricing_requirement, '') = ''",
        )
        _normalize_in_batches(
            doctype="AOS Category",
            field="sort_order",
            value=0,
            where_sql="sort_order IS NULL OR sort_order < 0",
        )
    if frappe.db.table_exists("AOS Category Attribute Row"):
        _normalize_in_batches(
            doctype="AOS Category Attribute Row",
            field="sort_order",
            value=0,
            where_sql="sort_order IS NULL OR sort_order < 0",
        )

    frappe.logger("aos.catalog", allow_site=True).info(
        "catalog_schema_hardening_complete indexes=%s",
        len(INDEXES),
    )


def _normalize_in_batches(*, doctype: str, field: str, value: Any, where_sql: str) -> None:
    """Update fixed allowlisted fields without committing inside the patch."""

    allowed = {
        ("AOS Category", "pricing_requirement"),
        ("AOS Category", "sort_order"),
        ("AOS Category Attribute Row", "sort_order"),
    }
    if (doctype, field) not in allowed:
        raise ValueError("Unsupported Catalog migration field")
    table = f"`tab{doctype}`"
    while True:
        rows = frappe.db.sql(
            f"SELECT name FROM {table} WHERE {where_sql} ORDER BY name LIMIT %s",
            (_BATCH_SIZE,),
            as_dict=True,
        )
        names = [str(row["name"]) for row in rows]
        if not names:
            return
        placeholders = ", ".join(["%s"] * len(names))
        frappe.db.sql(
            f"UPDATE {table} SET `{field}` = %s WHERE name IN ({placeholders})",
            (value, *names),
        )


def _index_exists(doctype: str, index_name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (f"tab{doctype}", index_name),
        )
    )
