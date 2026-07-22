"""Backfill lifecycle metadata and indexes for the hardened Media subsystem."""

from __future__ import annotations

import frappe

DOCTYPE = "AOS Media Object"
TABLE = "tabAOS Media Object"
INDEXES: dict[str, list[str]] = {
    "idx_aos_media_owner_purpose_status": ["owner_user", "purpose", "status", "creation"],
    "idx_aos_media_attachment": ["attached_doctype", "attached_name", "purpose", "status"],
    "idx_aos_media_cleanup": ["status", "modified", "creation"],
    "idx_aos_media_upload_expiry": ["status", "upload_expires_at"],
    "idx_aos_media_delete_retry": ["status", "delete_requested_at", "last_storage_attempt_at"],
    "idx_aos_media_idempotency": ["owner_user", "purpose", "idempotency_key_hash"],
    "idx_aos_media_derived": ["derived_from_media", "status"],
}


def execute() -> None:
    if not frappe.db.table_exists(DOCTYPE):
        return

    # Frappe's database adapter commits safely before schema DDL and performs
    # its own idempotency check. Raw ALTER TABLE through frappe.db.sql is
    # rejected during migrations once the transaction has pending writes.
    for name, fields in INDEXES.items():
        frappe.db.add_index(DOCTYPE, fields, index_name=name)

    # Preserve legacy records. These updates are idempotent and run only after
    # all indexes have been created successfully.
    frappe.db.sql(
        f"""
        UPDATE `{TABLE}`
        SET expected_size_bytes = size_bytes
        WHERE COALESCE(expected_size_bytes, 0) = 0
          AND COALESCE(size_bytes, 0) > 0
        """
    )
    frappe.db.sql(
        f"""
        UPDATE `{TABLE}`
        SET completed_at = uploaded_at
        WHERE completed_at IS NULL
          AND uploaded_at IS NOT NULL
          AND status IN ('Uploaded', 'Processing', 'Ready', 'Attached')
        """
    )
    frappe.db.sql(
        f"""
        UPDATE `{TABLE}`
        SET public_url = ''
        WHERE visibility = 'Private'
          AND COALESCE(public_url, '') != ''
        """
    )
    frappe.db.sql(
        f"""
        UPDATE `{TABLE}`
        SET upload_bucket = bucket,
            upload_object_key = object_key
        WHERE status = 'Initialized'
          AND (COALESCE(upload_bucket, '') = '' OR COALESCE(upload_object_key, '') = '')
        """
    )

    frappe.logger("aos.media", allow_site=True).info(
        "media_schema_hardening_complete indexes=%s",
        len(INDEXES),
    )
