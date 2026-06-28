"""Add indexes for seller near-me and map viewport discovery."""

from __future__ import annotations

import frappe


INDEXES = [
    {
        "name": "idx_aos_seller_status_location_country",
        "fields": ["status", "has_location", "country_code"],
    },
    {
        "name": "idx_aos_seller_status_location_lat_lon",
        "fields": ["status", "has_location", "latitude", "longitude"],
    },
    {
        "name": "idx_aos_seller_status_location_region_locality",
        "fields": ["status", "has_location", "region", "locality"],
    },
]


def execute():
    """Create non-unique seller location indexes when missing."""

    doctype = "AOS Seller"

    if not frappe.db.exists(
        "DocType",
        doctype,
    ):
        frappe.log_error(
            title="Seller Location Index Patch Skipped",
            message=f"DocType {doctype} does not exist. Skipping.",
        )
        return

    for index in INDEXES:
        _add_index_if_missing(
            doctype=doctype,
            index_name=index["name"],
            fields=index["fields"],
        )


def _add_index_if_missing(
    *,
    doctype: str,
    index_name: str,
    fields: list[str],
):
    """Add one DB index if it is missing."""

    _validate_fields_exist(
        doctype=doctype,
        fields=fields,
    )

    if _index_exists(
        doctype=doctype,
        index_name=index_name,
    ):
        return

    frappe.db.add_index(
        doctype,
        fields,
        index_name=index_name,
    )


def _validate_fields_exist(
    *,
    doctype: str,
    fields: list[str],
):
    """Ensure all index fields exist before migration."""

    meta = frappe.get_meta(
        doctype
    )

    valid_fields = {
        field.fieldname
        for field in meta.fields
    }
    valid_fields.add(
        "name"
    )

    missing_fields = [
        field
        for field in fields
        if field not in valid_fields
    ]

    if missing_fields:
        frappe.throw(
            f"Cannot add index on {doctype}. "
            f"These fields do not exist: {', '.join(missing_fields)}"
        )


def _index_exists(
    *,
    doctype: str,
    index_name: str,
) -> bool:
    """Return whether an index exists in the current database."""

    table_name = f"tab{doctype}"

    existing = frappe.db.sql(
        """
        SELECT INDEX_NAME
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
        LIMIT 1
        """,
        (
            table_name,
            index_name,
        ),
        as_dict=True,
    )

    return bool(existing)
