"""Scheduled cleanup for MinIO-backed AOS media objects."""

from __future__ import annotations

import frappe

from aos.services.media.media_service import MediaService


INITIALIZED_RETENTION_HOURS = 24
UNATTACHED_RETENTION_DAYS = 7
DELETE_PENDING_RETENTION_HOURS = 24
BATCH_LIMIT = 100


def cleanup_media_objects() -> int:
    """Clean abandoned or pending-delete AOS Media Object records.

    This is safe to run before feature migrations. If the DocType is not
    installed yet, it exits without error.
    """
    try:
        if not frappe.db.exists("DocType", "AOS Media Object"):
            return 0

        service = MediaService()
        total = 0
        total += service.cleanup_initialized(
            older_than_hours=INITIALIZED_RETENTION_HOURS,
            limit=BATCH_LIMIT,
        )
        total += service.cleanup_unattached_uploaded(
            older_than_days=UNATTACHED_RETENTION_DAYS,
            limit=BATCH_LIMIT,
        )
        total += service.cleanup_delete_pending(
            older_than_hours=DELETE_PENDING_RETENTION_HOURS,
            limit=BATCH_LIMIT,
        )

        if total:
            frappe.logger("aos").info(
                f"[AOS] Media cleanup deleted {total} abandoned media objects."
            )

        return total

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Media Cleanup Failed")
        return 0
