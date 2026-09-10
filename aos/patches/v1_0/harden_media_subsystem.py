"""Install composite indexes required by the current Media subsystem."""

from __future__ import annotations

import frappe

MEDIA_DOCTYPE = "AOS Media Object"
PROCESSING_DOCTYPE = "AOS Media Processing Job"

MEDIA_INDEXES: dict[str, list[str]] = {
    "idx_aos_media_owner_purpose_status": ["owner_user", "purpose", "status", "creation"],
    "idx_aos_media_attachment": ["attached_doctype", "attached_name", "purpose", "status"],
    "idx_aos_media_cleanup": ["status", "modified", "creation"],
    "idx_aos_media_upload_expiry": ["status", "upload_expires_at"],
    "idx_aos_media_delete_retry": ["status", "delete_requested_at", "last_storage_attempt_at"],
    "idx_aos_media_idempotency": ["owner_user", "purpose", "idempotency_key_hash"],
    "idx_aos_media_derived": ["derived_from_media", "status"],
    "idx_aos_media_staging_cleanup": ["staging_cleanup_required", "upload_expires_at", "modified"],
    "idx_aos_media_multipart_active": ["owner_user", "upload_mode", "status", "upload_expires_at"],
}

PROCESSING_INDEXES: dict[str, list[str]] = {
    "idx_aos_media_processing_owner_status": ["owner_user", "status", "creation"],
    "idx_aos_media_processing_source": ["source_media", "operation", "status"],
    "idx_aos_media_processing_recovery": ["status", "next_attempt_at", "started_at", "modified"],
}


def _index_exists(doctype: str, index_name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (f"tab{doctype}", index_name),
        )
    )


def _install_indexes(doctype: str, indexes: dict[str, list[str]]) -> int:
    if not frappe.db.table_exists(doctype):
        return 0
    installed = 0
    for name, fields in indexes.items():
        if _index_exists(doctype, name):
            continue
        frappe.db.add_index(doctype, fields, index_name=name)
        installed += 1
    return installed


def execute() -> None:
    """Install the canonical query indexes after DocType model synchronization."""
    installed = _install_indexes(MEDIA_DOCTYPE, MEDIA_INDEXES)
    installed += _install_indexes(PROCESSING_DOCTYPE, PROCESSING_INDEXES)
    frappe.logger("aos.media", allow_site=True).info(
        "media_schema_indexes_ready count=%s",
        installed,
    )
