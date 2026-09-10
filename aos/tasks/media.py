"""Bounded, retry-safe cleanup for AOS Media Object lifecycle records."""

from __future__ import annotations

import frappe

from aos.services.media.media_service import MediaService
from aos.services.media.background_processing import MediaProcessingService
from aos.utils.aos_config import get_env_int


def finalize_media_deletion(media_id: str) -> str:
    """Finalize one committed Delete Pending row from the Media worker queue."""
    try:
        if not frappe.db.exists("DocType", "AOS Media Object"):
            return "missing_doctype"
        doc = MediaService().finalize_delete_as_system(media_id=media_id)
        return str(getattr(doc, "status", ""))
    except Exception:
        # Raising lets the queue record the failure; the durable Delete Pending
        # state remains eligible for the scheduled reconciliation pass.
        frappe.log_error(frappe.get_traceback(), "AOS Media Deletion Job Failed")
        raise


def process_media_processing_job(media_processing_job_id: str) -> str:
    """Execute one durable Media processing job on the long Frappe queue."""
    return MediaProcessingService().process(job_id=media_processing_job_id)


def recover_media_processing_jobs() -> int:
    """Re-enqueue due/retryable/stale Media processing jobs in bounded batches."""
    limit = get_env_int("AOS_MEDIA_PROCESSING_RECOVERY_LIMIT", 100, min_value=10, max_value=500)
    try:
        return MediaProcessingService().recover_due_jobs(limit=limit)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Media Processing Recovery Failed")
        return 0


def cleanup_media_objects() -> int:
    """Delete only unreferenced/expired media through privileged service paths."""
    try:
        if not frappe.db.exists("DocType", "AOS Media Object"):
            return 0

        initialized_hours = get_env_int(
            "AOS_MEDIA_INITIALIZED_RETENTION_HOURS", 24, min_value=1, max_value=168
        )
        unattached_days = get_env_int(
            "AOS_MEDIA_UNATTACHED_RETENTION_DAYS", 7, min_value=1, max_value=90
        )
        delete_retry_hours = get_env_int(
            "AOS_MEDIA_DELETE_RETRY_HOURS", 1, min_value=1, max_value=24
        )
        batch_limit = get_env_int(
            "AOS_MEDIA_CLEANUP_BATCH_LIMIT", 100, min_value=10, max_value=1000
        )

        service = MediaService()
        counters = {
            "expired": service.cleanup_expired_upload_sessions(limit=batch_limit),
            "initialized": service.cleanup_initialized(
                older_than_hours=initialized_hours,
                limit=batch_limit,
            ),
            "unattached": service.cleanup_unattached_uploaded(
                older_than_days=unattached_days,
                limit=batch_limit,
            ),
            "delete_pending": service.cleanup_delete_pending(
                older_than_hours=delete_retry_hours,
                limit=batch_limit,
            ),
            "staging": service.cleanup_staging_objects(limit=batch_limit),
        }
        total = sum(counters.values())
        frappe.logger("aos.media", allow_site=True).info(
            "media_cleanup_complete expired=%s initialized=%s unattached=%s delete_pending=%s staging=%s total=%s",
            counters["expired"],
            counters["initialized"],
            counters["unattached"],
            counters["delete_pending"],
            counters["staging"],
            total,
        )
        return total
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Media Cleanup Failed")
        return 0
