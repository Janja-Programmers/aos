"""Backfill Firebase registration-kind metadata for legacy AOS push rows.

Existing mobile/web rows predate Firebase Installation ID support and therefore
represent legacy FCM registration tokens. This post-model-sync patch is
idempotent, data-only, and never rewrites the provider registration identifier.
"""

from __future__ import annotations

import frappe


def execute() -> None:
    if not frappe.db.table_exists("AOS Push Token"):
        return
    if not frappe.db.has_column("AOS Push Token", "registration_kind"):
        return

    # Rows created before this field existed are registration-token targets.
    frappe.db.sql(
        """
        UPDATE `tabAOS Push Token`
        SET registration_kind = 'token'
        WHERE COALESCE(registration_kind, '') = ''
        """
    )

    # Fail closed for any manually-corrupted value rather than guessing whether
    # an opaque provider identifier is a token or a FID.
    frappe.db.sql(
        """
        UPDATE `tabAOS Push Token`
        SET is_active = 0, active_device_key = NULL
        WHERE registration_kind NOT IN ('token', 'fid')
          AND COALESCE(is_active, 0) = 1
        """
    )
