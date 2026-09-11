"""Idempotent schema-only indexes for the canonical Catalog model.

This installer is safe to run both as a fresh-site patch and from after_migrate,
where it reasserts manual indexes after Frappe DocType synchronization.
"""

from __future__ import annotations

from collections.abc import Sequence

import frappe

INDEXES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    (
        "AOS Category",
        "idx_catalog_order",
        ("sort_order", "category_name", "name"),
        False,
    ),
    (
        "AOS Category",
        "idx_catalog_parent_active_order",
        ("parent_aos_category", "is_active", "is_group", "sort_order", "name"),
        False,
    ),
    (
        "AOS Category Attribute Row",
        "idx_catalog_category_attribute_order",
        ("parenttype", "parentfield", "parent", "sort_order", "idx", "name"),
        False,
    ),
    (
        "AOS Category Attribute Row",
        "uq_catalog_category_attribute",
        ("parent", "parenttype", "parentfield", "attribute"),
        True,
    ),
    (
        "AOS Category Attribute Row",
        "idx_catalog_attribute_category_reference",
        ("attribute", "parent", "parenttype", "parentfield"),
        False,
    ),
    (
        "AOS Category Attribute Row",
        "idx_catalog_dependency_parent_reference",
        ("depends_on_attribute", "parent", "parenttype", "parentfield"),
        False,
    ),
    (
        "AOS Category Attribute Dependency Row",
        "idx_catalog_dependency_mapping_order",
        ("parenttype", "parentfield", "parent", "child_attribute", "idx", "name"),
        False,
    ),
    (
        "AOS Category Attribute Dependency Row",
        "uq_catalog_dependency_mapping",
        ("mapping_key",),
        True,
    ),
    (
        "AOS Category Attribute Dependency Row",
        "idx_catalog_dependency_child_reference",
        ("child_attribute", "parent", "parenttype", "parentfield"),
        False,
    ),
    (
        "AOS Ad Attribute",
        "idx_catalog_attribute_active_key",
        ("is_active", "attribute_key", "name"),
        False,
    ),
    (
        "AOS Ad Attribute Value",
        "idx_catalog_ad_attribute_reference",
        ("attribute", "parent"),
        False,
    ),
    (
        "AOS Ad",
        "idx_catalog_ad_category_reference",
        ("category", "name"),
        False,
    ),
)


def execute() -> None:
    for doctype, name, columns, unique in INDEXES:
        _ensure_index(doctype, name, columns, unique=unique)


def _table(doctype: str) -> str:
    return f"tab{doctype}"


def _ensure_columns(doctype: str, columns: Sequence[str]) -> None:
    if not frappe.db.table_exists(doctype):
        frappe.throw(f"Catalog schema table is missing: {doctype}")
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(f"Catalog schema columns are missing for {doctype}: {', '.join(missing)}")


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
    columns = tuple(str(row["COLUMN_NAME"]) for row in rows)
    unique = not bool(int(rows[0]["NON_UNIQUE"]))
    return columns, unique


def _assert_unique_ready(doctype: str, columns: Sequence[str], name: str) -> None:
    non_null = " AND ".join(f"`{column}` IS NOT NULL AND `{column}`!=''" for column in columns)
    groups = ", ".join(f"`{column}`" for column in columns)
    duplicate = frappe.db.sql(
        f"""
        SELECT 1 FROM `{_table(doctype)}`
        WHERE {non_null}
        GROUP BY {groups} HAVING COUNT(*)>1 LIMIT 1
        """
    )
    if duplicate:
        frappe.throw(f"Cannot install Catalog unique index {name}; duplicate data remains in {doctype}")


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
