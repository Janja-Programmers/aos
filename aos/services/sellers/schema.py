"""Current idempotent Seller schema invariants for fresh installs."""

from __future__ import annotations

import frappe

SELLER_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("idx_aos_seller_public_discovery", ("status", "seller_type", "rating", "creation", "name")),
    ("idx_aos_seller_category_discovery", ("status", "business_category", "creation", "name")),
    ("idx_aos_seller_ad_discovery", ("status", "total_ads", "creation", "name")),
    ("idx_aos_seller_review_discovery", ("status", "total_reviews", "rating", "name")),
    ("idx_aos_seller_location_country", ("status", "has_location", "country_code", "name")),
    ("idx_aos_seller_location_lat_lon", ("status", "has_location", "latitude", "longitude")),
    ("idx_aos_seller_location_region", ("status", "has_location", "region", "locality", "name")),
)


def execute() -> None:
    doctype = "AOS Seller"
    if not frappe.db.exists("DocType", doctype):
        return
    # Validate against the physical table, not ``DocType.meta.fields``. Frappe's
    # metadata field list excludes standard columns such as ``name`` and
    # ``creation`` even though they are real columns and valid index members.
    # ``after_migrate`` runs after DocType synchronization, so the table is the
    # authoritative schema at this point.
    table_columns = set(frappe.db.get_table_columns(doctype))
    for index_name, fields in SELLER_INDEXES:
        missing = set(fields) - table_columns
        if missing:
            frappe.throw(
                f"Cannot install Seller index {index_name}: schema fields are missing: "
                f"{', '.join(sorted(missing))}."
            )
        if not _index_exists(index_name):
            frappe.db.add_index(doctype, list(fields), index_name=index_name)


def _index_exists(index_name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1 FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'tabAOS Seller'
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (index_name,),
        )
    )
