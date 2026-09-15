"""Idempotent schema-only indexes for canonical Marketplace Discovery.

Safe for fresh-site patching and after_migrate reassertion. This module does no
historical data repair, alias migration, or compatibility backfill.
"""
from __future__ import annotations

from collections.abc import Sequence
import frappe

INDEXES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("AOS Ad", "idx_market_ad_public_recent", ("status", "expires_on", "creation", "public_id"), False),
    ("AOS Ad", "idx_market_ad_category_recent", ("status", "category", "creation", "public_id"), False),
    ("AOS Ad", "idx_market_ad_country_category_recent", ("status", "country", "category", "creation", "public_id"), False),
    ("AOS Ad", "idx_market_ad_location_recent", ("status", "location", "creation", "public_id"), False),
    ("AOS Ad", "idx_market_ad_seller_status", ("seller", "status", "modified", "public_id"), False),
    ("AOS Ad", "idx_market_ad_seller_name", ("seller", "name"), False),
    ("AOS Ad", "idx_market_ad_price_filter", ("status", "currency", "price_type", "price", "public_id"), False),
    ("AOS Ad", "idx_market_ad_expiry", ("status", "expires_on", "public_id"), False),
    ("AOS Ad Image", "idx_market_ad_image_order", ("parent", "parenttype", "parentfield", "is_primary", "sort_order", "name"), False),
    ("AOS Ad Image", "uq_market_ad_image_media", ("parent", "parenttype", "parentfield", "media"), True),
    ("AOS Ad Attribute Value", "idx_market_ad_attribute_parent", ("parent", "parenttype", "parentfield", "idx", "name"), False),
    ("AOS Ad Attribute Value", "uq_market_ad_attribute", ("parent", "parenttype", "parentfield", "attribute"), True),
    ("AOS Ad Draft", "idx_market_ad_draft_owner", ("user", "status", "modified", "public_id"), False),
    ("AOS Saved Search", "idx_market_saved_search_owner", ("user", "is_active", "modified", "public_id"), False),
    ("AOS Saved Search", "idx_market_saved_search_fingerprint", ("user", "is_active", "fingerprint"), False),
    ("AOS Search Index Job", "idx_market_search_job_target", ("target_doctype", "target_name", "index_kind", "action", "creation"), False),
    ("AOS Search Index Job", "idx_market_search_job_state", ("status", "modified", "name"), False),
    ("AOS Exchange Rate", "idx_market_fx_snapshot", ("base_currency", "rate_version", "currency"), False),
)


def execute() -> None:
    for doctype, name, columns, unique in INDEXES:
        _ensure_index(doctype, name, columns, unique=unique)


def _table(doctype: str) -> str:
    return f"tab{doctype}"


def _ensure_columns(doctype: str, columns: Sequence[str]) -> None:
    if not frappe.db.table_exists(doctype):
        frappe.throw(f"Marketplace Discovery schema table is missing: {doctype}")
    missing=[column for column in columns if not frappe.db.has_column(doctype,column)]
    if missing:
        frappe.throw(f"Marketplace Discovery schema columns are missing for {doctype}: {', '.join(missing)}")


def _index_definition(doctype: str, name: str) -> tuple[tuple[str, ...], bool] | None:
    rows=frappe.db.sql(
        """SELECT COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS
           WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
           ORDER BY SEQ_IN_INDEX""",
        (_table(doctype),name), as_dict=True,
    )
    if not rows: return None
    return tuple(str(row["COLUMN_NAME"]) for row in rows), not bool(int(rows[0]["NON_UNIQUE"]))


def _assert_unique_ready(doctype: str, columns: Sequence[str], name: str) -> None:
    non_null=" AND ".join(f"`{column}` IS NOT NULL AND `{column}`!=''" for column in columns)
    groups=", ".join(f"`{column}`" for column in columns)
    if frappe.db.sql(f"SELECT 1 FROM `{_table(doctype)}` WHERE {non_null} GROUP BY {groups} HAVING COUNT(*)>1 LIMIT 1"):
        frappe.throw(f"Cannot install Marketplace Discovery unique index {name}; duplicate data exists in {doctype}")


def _drop_index(doctype: str, name: str) -> None:
    db_type=str(getattr(frappe.db,"db_type","mariadb") or "mariadb").lower()
    if db_type == "postgres": frappe.db.sql_ddl(f'DROP INDEX IF EXISTS "{name}"')
    else: frappe.db.sql_ddl(f"ALTER TABLE `{_table(doctype)}` DROP INDEX `{name}`")


def _ensure_index(doctype: str, name: str, columns: tuple[str, ...], *, unique: bool) -> None:
    _ensure_columns(doctype,columns); expected=(columns,unique); existing=_index_definition(doctype,name)
    if existing == expected: return
    if unique: _assert_unique_ready(doctype,columns,name)
    if existing is not None: _drop_index(doctype,name)
    if unique: frappe.db.add_unique(doctype,list(columns),constraint_name=name)
    else: frappe.db.add_index(doctype,list(columns),index_name=name)
