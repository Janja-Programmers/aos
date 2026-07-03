from __future__ import annotations

import frappe

from aos.services.video_service import VideoService
from aos.services.ranking_service import RankingService


def process_short_task(short_id: str, force: bool = False):
    """Compatibility task for older queued jobs.

    Heavy FFmpeg processing no longer runs in Frappe. This task now creates an
    AOS Video Processing Job and dispatches it to the external video service.
    New code should call ``create_video_processing_job`` directly.
    """
    if not short_id:
        return

    try:
        VideoService.process_short(short_id, force=force)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"process_short_task dispatch failed for short {short_id}",
        )


def update_short_score_task(short_id: str):
    """Update ranking score."""
    if not short_id:
        return

    try:
        RankingService.update_short_score(short_id)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"update_short_score_task failed for short {short_id}",
        )
