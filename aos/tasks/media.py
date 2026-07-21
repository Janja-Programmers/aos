"""Bounded, retry-safe cleanup for AOS Media Object lifecycle records."""

from __future__ import annotations

import frappe

from aos.services.media.media_service import MediaService
from aos.utils.aos_config import get_env_int


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
            "media_cleanup_complete initialized=%s unattached=%s delete_pending=%s staging=%s total=%s",
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
