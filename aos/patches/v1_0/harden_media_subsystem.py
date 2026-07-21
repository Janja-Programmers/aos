"""Backfill lifecycle metadata and indexes for the hardened Media subsystem."""

from __future__ import annotations

import frappe

INDEXES = {
    "idx_aos_media_owner_purpose_status": "(`owner_user`, `purpose`, `status`, `creation`)",
    "idx_aos_media_attachment": "(`attached_doctype`, `attached_name`, `purpose`, `status`)",
    "idx_aos_media_cleanup": "(`status`, `modified`, `creation`)",
    "idx_aos_media_upload_expiry": "(`status`, `upload_expires_at`)",
    "idx_aos_media_delete_retry": "(`status`, `delete_requested_at`, `last_storage_attempt_at`)",
    "idx_aos_media_idempotency": "(`owner_user`, `purpose`, `idempotency_key_hash`)",
    "idx_aos_media_derived": "(`derived_from_media`, `status`)",
}


def _index_exists(table: str, name: str) -> bool:
    return bool(frappe.db.sql(f"SHOW INDEX FROM `{table}` WHERE Key_name = %s", (name,)))


def execute() -> None:
    if not frappe.db.table_exists("AOS Media Object"):
        return
    table = "tabAOS Media Object"

    # Preserve legacy records. The patch only fills newly explicit lifecycle
    # metadata and removes a URL cache that was unsafe for private objects.
    frappe.db.sql(
        f"""
        UPDATE `{table}`
        SET expected_size_bytes = size_bytes
        WHERE COALESCE(expected_size_bytes, 0) = 0
          AND COALESCE(size_bytes, 0) > 0
        """
    )
    frappe.db.sql(
        f"""
        UPDATE `{table}`
        SET completed_at = uploaded_at
        WHERE completed_at IS NULL
          AND uploaded_at IS NOT NULL
          AND status IN ('Uploaded', 'Processing', 'Ready', 'Attached')
        """
    )
    frappe.db.sql(
        f"""
        UPDATE `{table}`
        SET public_url = ''
        WHERE visibility = 'Private'
          AND COALESCE(public_url, '') != ''
        """
    )
    frappe.db.sql(
        f"""
        UPDATE `{table}`
        SET upload_bucket = bucket,
            upload_object_key = object_key
        WHERE status = 'Initialized'
          AND (COALESCE(upload_bucket, '') = '' OR COALESCE(upload_object_key, '') = '')
        """
    )

    for name, columns in INDEXES.items():
        if not _index_exists(table, name):
            frappe.db.sql(f"ALTER TABLE `{table}` ADD INDEX `{name}` {columns}")

    frappe.logger("aos.media", allow_site=True).info(
        "media_schema_hardening_complete indexes=%s",
        len(INDEXES),
    )
