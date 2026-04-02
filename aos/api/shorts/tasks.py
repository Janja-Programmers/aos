from __future__ import annotations

import frappe

from aos.services.video_service import VideoService
from aos.services.ranking_service import RankingService
from aos.services.notification_service import NotificationService


def process_short_task(short_id: str):
    """Process uploaded short (transcoding, thumbnail, etc.)"""
    if not short_id:
        return

    try:
        # Get doc BEFORE processing
        doc = frappe.get_doc("AOS Short", short_id)
        previous_status = doc.status

        VideoService.process_short(short_id)

        # Reload doc AFTER processing
        doc.reload()

        # TRIGGER NOTIFICATION ONLY ON FIRST READY
        if previous_status != "ready" and doc.status == "ready":
            if doc.seller:
                NotificationService.notify_new_short(
                    actor=doc.seller,
                    short_id=doc.name,
                )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"process_short_task failed for short {short_id}",
        )


def update_short_score_task(short_id: str):
    """Update ranking score"""
    if not short_id:
        return

    try:
        RankingService.update_short_score(short_id)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"update_short_score_task failed for short {short_id}",
        )
