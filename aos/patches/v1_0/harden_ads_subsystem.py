"""Harden Ads persistence, indexes, uniqueness, and historical snapshots.

The patch is additive and repeatable. Exact duplicate user-action/child rows are
collapsed deterministically before composite unique constraints are added; the
oldest row is retained and no distinct business record is removed.
"""

from __future__ import annotations

import frappe

from aos.services.catalog.service import attribute_key

_BATCH_SIZE = 250

_INDEXES: dict[str, tuple[str, list[str]]] = {
    "idx_aos_ad_public_recent": ("AOS Ad", ["status", "expires_on", "creation", "name"]),
    "idx_aos_ad_public_market": ("AOS Ad", ["status", "country", "category", "creation"]),
    "idx_aos_ad_seller_status": ("AOS Ad", ["seller", "status", "modified", "name"]),
    "idx_aos_ad_location_status": ("AOS Ad", ["location", "status", "creation"]),
    "idx_aos_ad_price_filter": ("AOS Ad", ["status", "currency", "price_type", "price"]),
    "idx_aos_ad_expiry": ("AOS Ad", ["status", "expires_on", "name"]),
    "idx_aos_ad_image_parent_order": (
        "AOS Ad Image",
        ["parent", "parenttype", "parentfield", "is_primary", "sort_order"],
    ),
    "idx_aos_ad_detail_parent": (
        "AOS Ad Attribute Value",
        ["parent", "parenttype", "parentfield", "idx"],
    ),
    "idx_aos_ad_draft_owner_status": ("AOS Ad Draft", ["user", "status", "modified", "name"]),
    "idx_aos_wishlist_owner_status": ("AOS Wishlist", ["user", "status", "creation", "name"]),
    "idx_aos_report_backlog": ("AOS Ad Report", ["status", "creation", "name"]),
    "idx_aos_report_ad_status": ("AOS Ad Report", ["ad", "status", "creation"]),
}

_UNIQUES: dict[str, tuple[str, list[str]]] = {
    "uq_aos_wishlist_user_ad": ("AOS Wishlist", ["user", "ad"]),
    "uq_aos_ad_report_user_ad": ("AOS Ad Report", ["ad", "reported_by"]),
    "uq_aos_ad_image_media": (
        "AOS Ad Image",
        ["parent", "parenttype", "parentfield", "media"],
    ),
    "uq_aos_ad_detail_attribute": (
        "AOS Ad Attribute Value",
        ["parent", "parenttype", "parentfield", "attribute"],
    ),
}


def execute() -> None:
    _sync_ads_doctypes()

    for name, (doctype, fields) in _INDEXES.items():
        if _supports(doctype, fields):
            frappe.db.add_index(doctype, fields, index_name=name)

    _deduplicate("AOS Wishlist", ["user", "ad"])
    _deduplicate("AOS Ad Report", ["ad", "reported_by"])
    _deduplicate(
        "AOS Ad Image",
        ["parent", "parenttype", "parentfield", "media"],
        required_nonempty=["media"],
    )
    _deduplicate(
        "AOS Ad Attribute Value",
        ["parent", "parenttype", "parentfield", "attribute"],
        required_nonempty=["attribute"],
    )
    _backfill_attribute_snapshots()
    _backfill_status_timestamps()

    for name, (doctype, fields) in _UNIQUES.items():
        if _supports(doctype, fields) and not _index_exists(doctype, name):
            frappe.db.add_unique(doctype, fields, constraint_name=name)

    frappe.logger("aos.ads", allow_site=True).info(
        "ads_schema_hardening_complete indexes=%s unique_constraints=%s",
        len(_INDEXES),
        len(_UNIQUES),
    )


def _sync_ads_doctypes() -> None:
    """Load source-controlled schema before any column-aware migration work."""

    for doctype_name in ("aos_ad_attribute_value", "aos_ad"):
        frappe.reload_doc("aos", "doctype", doctype_name, force=True)
    frappe.clear_cache(doctype="AOS Ad Attribute Value")
    frappe.clear_cache(doctype="AOS Ad")


def _supports(doctype: str, fields: list[str]) -> bool:
    return bool(
        frappe.db.table_exists(doctype)
        and all(frappe.db.has_column(doctype, field) for field in fields)
    )


def _index_exists(doctype: str, name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1 FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (f"tab{doctype}", name),
        )
    )


def _deduplicate(
    doctype: str,
    fields: list[str],
    *,
    required_nonempty: list[str] | None = None,
) -> None:
    if not _supports(doctype, fields):
        return
    table = f"`tab{doctype}`"
    required = required_nonempty or []
    predicates = [f"COALESCE(`{field}`, '') != ''" for field in required]
    where = f"WHERE {' AND '.join(predicates)}" if predicates else ""
    field_sql = ", ".join(f"`{field}`" for field in fields)
    while True:
        groups = frappe.db.sql(
            f"""
            SELECT {field_sql}
            FROM {table}
            {where}
            GROUP BY {field_sql}
            HAVING COUNT(*) > 1
            ORDER BY {field_sql}
            LIMIT %s
            """,
            (_BATCH_SIZE,),
            as_dict=True,
        )
        if not groups:
            return
        for group in groups:
            conditions = " AND ".join(f"`{field}` <=> %s" for field in fields)
            values = tuple(group[field] for field in fields)
            rows = frappe.db.sql(
                f"SELECT name FROM {table} WHERE {conditions} ORDER BY creation ASC, name ASC",
                values,
                as_dict=True,
            )
            for duplicate in rows[1:]:
                if doctype in {"AOS Ad Image", "AOS Ad Attribute Value"}:
                    frappe.db.sql(f"DELETE FROM {table} WHERE name = %s", (duplicate.name,))
                else:
                    frappe.delete_doc(doctype, duplicate.name, ignore_permissions=True, force=True)


def _backfill_attribute_snapshots() -> None:
    doctype = "AOS Ad Attribute Value"
    fields = ["attribute_key", "attribute_label", "attribute_type", "attribute_unit"]
    if not _supports(doctype, ["attribute", *fields]):
        return
    while True:
        rows = frappe.db.sql(
            """
            SELECT v.name, v.attribute, a.label, a.field_type, a.unit
            FROM `tabAOS Ad Attribute Value` v
            LEFT JOIN `tabAOS Ad Attribute` a ON a.name = v.attribute
            WHERE COALESCE(v.attribute_key, '') = ''
               OR COALESCE(v.attribute_label, '') = ''
               OR COALESCE(v.attribute_type, '') = ''
            ORDER BY v.name
            LIMIT %s
            """,
            (_BATCH_SIZE,),
            as_dict=True,
        )
        if not rows:
            return
        for row in rows:
            frappe.db.set_value(
                doctype,
                row.name,
                {
                    "attribute_key": attribute_key(row.attribute) or "legacy_attribute",
                    "attribute_label": str(row.label or row.attribute or "Legacy attribute")[:140],
                    "attribute_type": str(row.field_type or "Text")[:140],
                    "attribute_unit": str(row.unit or "")[:140],
                },
                update_modified=False,
            )


def _backfill_status_timestamps() -> None:
    doctype = "AOS Ad"
    fields = [
        "status_changed_on",
        "published_on",
        "sold_on",
        "renewed_on",
        "expired_on",
        "deleted_on",
    ]
    if not _supports(doctype, ["status", "creation", "modified", *fields]):
        return
    frappe.db.sql(
        """
        UPDATE `tabAOS Ad`
        SET status_changed_on = COALESCE(status_changed_on, modified, creation),
            published_on = CASE WHEN status = 'Active' THEN COALESCE(published_on, reviewed_on, modified, creation) ELSE published_on END,
            sold_on = CASE WHEN status = 'Sold' THEN COALESCE(sold_on, modified, creation) ELSE sold_on END,
            expired_on = CASE WHEN status = 'Expired' THEN COALESCE(expired_on, modified, creation) ELSE expired_on END,
            deleted_on = CASE WHEN status = 'Deleted' THEN COALESCE(deleted_on, modified, creation) ELSE deleted_on END
        WHERE status_changed_on IS NULL
           OR (status = 'Active' AND published_on IS NULL)
           OR (status = 'Sold' AND sold_on IS NULL)
           OR (status = 'Expired' AND expired_on IS NULL)
           OR (status = 'Deleted' AND deleted_on IS NULL)
        """
    )
