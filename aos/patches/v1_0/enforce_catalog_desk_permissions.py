"""Restore source-controlled, administrator-only Catalog Desk permissions.

Older sites may retain ``Custom DocPerm`` overrides that grant Catalog Desk
access to roles such as ``AOS Moderator`` even though the DocType JSON grants
CRUD access only to ``System Manager``. Frappe merges those persisted rows into
runtime metadata, so the overrides must be removed during migration.

The patch is intentionally separate from ``harden_catalog_subsystem`` because
that earlier patch may already be recorded as executed on upgraded sites. It is
idempotent, performs no commit, and clears only the affected DocType caches.
"""

from __future__ import annotations

import frappe

CATALOG_ADMIN_DOCTYPES = ("AOS Category", "AOS Ad Attribute")


def execute() -> None:
    if not frappe.db.table_exists("Custom DocPerm"):
        return

    placeholders = ", ".join(["%s"] * len(CATALOG_ADMIN_DOCTYPES))
    frappe.db.sql(
        f"""
        DELETE FROM `tabCustom DocPerm`
        WHERE parent IN ({placeholders})
        """,
        CATALOG_ADMIN_DOCTYPES,
    )

    for doctype in CATALOG_ADMIN_DOCTYPES:
        frappe.clear_cache(doctype=doctype)

    frappe.logger("aos.catalog", allow_site=True).info(
        "catalog_desk_permissions_hardened doctypes=%s",
        len(CATALOG_ADMIN_DOCTYPES),
    )
