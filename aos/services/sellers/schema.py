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
    meta = frappe.get_meta(doctype)
    valid_fields = {field.fieldname for field in meta.fields} | {"name"}
    for index_name, fields in SELLER_INDEXES:
        if not set(fields) <= valid_fields:
            frappe.throw(f"Cannot install Seller index {index_name}: schema fields are missing.")
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
