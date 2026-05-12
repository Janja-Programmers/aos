from __future__ import annotations

import frappe

from aos.services.video_service import VideoService
from aos.services.ranking_service import RankingService


def process_short_task(short_id: str):
    """
    Process uploaded short.

    Responsibilities:
    - Transcoding
    - Thumbnail generation
    - Playback URL generation
    - Duration extraction
    - Marking the short ready/failed through VideoService

    Important:
    - Do not notify followers here.
    - At processing time, the short is usually still hidden and metadata may not exist yet.
    - New-short notifications should happen when the short is published/visible.
    """
    if not short_id:
        return

    try:
        VideoService.process_short(short_id)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"process_short_task failed for short {short_id}",
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
