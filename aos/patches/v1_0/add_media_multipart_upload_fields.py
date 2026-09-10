"""Install the composite index used by active Media multipart-session queries."""

from __future__ import annotations

import frappe

DOCTYPE = "AOS Media Object"
INDEX_NAME = "idx_aos_media_multipart_active"
INDEX_FIELDS = ["owner_user", "upload_mode", "status", "upload_expires_at"]


def execute() -> None:
    if not frappe.db.table_exists(DOCTYPE):
        return
    frappe.db.add_index(DOCTYPE, INDEX_FIELDS, index_name=INDEX_NAME)
    frappe.logger("aos.media", allow_site=True).info(
        "media_multipart_index_ready index=%s",
        INDEX_NAME,
    )
