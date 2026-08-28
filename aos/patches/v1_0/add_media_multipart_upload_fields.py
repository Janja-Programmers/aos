"""Backfill and index resumable multipart upload metadata on upgraded sites."""

from __future__ import annotations

import frappe

DOCTYPE = "AOS Media Object"
TABLE = "tabAOS Media Object"
INDEX_NAME = "idx_aos_media_multipart_active"
INDEX_FIELDS = ["owner_user", "upload_mode", "status", "upload_expires_at"]


def execute() -> None:
    if not frappe.db.table_exists(DOCTYPE):
        return

    # This patch is registered under [post_model_sync], so the new DocType
    # fields exist before this backfill/index step runs on upgraded sites.
    # Explicitly backfill legacy rows so application
    # code never has to infer the transport used by old uploads.
    frappe.db.sql(
        f"""
        UPDATE `{TABLE}`
        SET upload_mode = 'direct'
        WHERE COALESCE(upload_mode, '') = ''
        """
    )
    frappe.db.sql(
        f"""
        UPDATE `{TABLE}`
        SET multipart_part_size_bytes = 0,
            multipart_part_count = 0
        WHERE upload_mode = 'direct'
        """
    )

    frappe.db.add_index(DOCTYPE, INDEX_FIELDS, index_name=INDEX_NAME)
    frappe.logger("aos.media", allow_site=True).info(
        "media_multipart_schema_ready index=%s",
        INDEX_NAME,
    )
