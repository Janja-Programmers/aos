"""Normalize localization duplicates and add production query constraints."""

from __future__ import annotations

import frappe


INDEXES = (
    ("AOS Location", ["country"], "idx_aos_location_country", False),
    ("AOS Location", ["country", "is_active"], "idx_aos_location_country_active", False),
    ("AOS Location", ["country", "location"], "unique_aos_location_country_location", True),
    ("AOS User Preference", ["user"], "unique_aos_user_preference_user", True),
    ("AOS User Preference", ["country"], "idx_aos_user_preference_country", False),
    ("AOS User Preference", ["currency"], "idx_aos_user_preference_currency", False),
    ("AOS User Preference", ["language"], "idx_aos_user_preference_language", False),
)


def execute():
    _dedupe("AOS Location", ["country", "location"])
    _dedupe("AOS User Preference", ["user"])
    _drop_legacy_location_unique_index()
    for doctype, fields, name, unique in INDEXES:
        if not frappe.db.exists("DocType", doctype) or _index_exists(doctype, name):
            continue
        if unique:
            frappe.db.add_unique(doctype, fields, constraint_name=name)
        else:
            frappe.db.add_index(doctype, fields, index_name=name)


def _dedupe(doctype: str, fields: list[str]):
    if not frappe.db.exists("DocType", doctype):
        return
    columns = ", ".join(f"`{field}`" for field in fields)
    rows = frappe.db.sql(f"SELECT {columns}, GROUP_CONCAT(name ORDER BY modified DESC, name DESC) names FROM `tab{doctype}` GROUP BY {columns} HAVING COUNT(*) > 1", as_dict=True)
    for row in rows:
        for name in str(row.names or "").split(",")[1:]:
            frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)


def _index_exists(doctype: str, name: str) -> bool:
    return bool(frappe.db.sql("SELECT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1", (f"tab{doctype}", name)))


def _drop_legacy_location_unique_index():
    """Remove only the old single-column unique(location), never broad indexes."""
    rows = frappe.db.sql(
        """SELECT INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) columns_csv
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='tabAOS Location' AND NON_UNIQUE=0 AND INDEX_NAME!='PRIMARY'
        GROUP BY INDEX_NAME""",
        as_dict=True,
    )
    for row in rows:
        if row.columns_csv == "location":
            safe_name = str(row.INDEX_NAME).replace("`", "``")
            frappe.db.sql(f"ALTER TABLE `tabAOS Location` DROP INDEX `{safe_name}`")
