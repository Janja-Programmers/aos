from __future__ import annotations

import frappe

from aos.services.ranking_service import RankingService


def update_short_score_task(short_id: str):
    """Update ranking score."""
    if not short_id:
        return

    try:
        RankingService.update_short_score(short_id)

    except Exception:
        frappe.log_error(
            "Shorts operation failed.",
            "Short score update task failed",
        )
