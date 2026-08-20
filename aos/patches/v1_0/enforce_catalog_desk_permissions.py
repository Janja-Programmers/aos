"""Legacy Catalog permission migration retained for patch-history compatibility.

Catalog Desk authorization is now intentionally owned by Frappe's permission
engine (DocPerm / Custom DocPerm / Role Permissions Manager). This historical
patch must therefore never delete administrator-configured Custom DocPerm rows.
Sites where the patch already ran remain unaffected; sites that have not run it
will preserve any role grants configured before migration.
"""

from __future__ import annotations

import frappe

CATALOG_ROLE_MANAGED_DOCTYPES = ("AOS Category", "AOS Ad Attribute")


def execute() -> None:
    for doctype in CATALOG_ROLE_MANAGED_DOCTYPES:
        frappe.clear_cache(doctype=doctype)

    frappe.logger("aos.catalog", allow_site=True).info(
        "catalog_desk_permissions_role_managed doctypes=%s",
        len(CATALOG_ROLE_MANAGED_DOCTYPES),
    )
