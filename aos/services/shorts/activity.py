"""Private Activity integration for the current Shorts producer surface.

Shorts owns content lifecycle, visibility, moderation and analytics.  Activity
only retains the existing user-facing report-history event; unused historical
watch/like/comment/repost helpers are deliberately absent.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.activity.producer import best_effort_activity
from aos.services.activity_service import ActivityService
from aos.services.media.media_service import MediaService

SHORT_DOCTYPE = "AOS Short"
SHORT_REPORT_DOCTYPE = "AOS Short Report"
SHORT_ACTIVITY_GROUP = "Shorts"
SHORT_REPORT_ACTIVITY = "short_report"
ROUTE_TYPE_SHORT = "short"


def short_report_unique_key(report_id: str) -> str:
    # Report id is a hidden producer idempotency identity; the Activity target
    # remains the canonical Short.
    return ActivityService.build_unique_key(
        activity_type=SHORT_REPORT_ACTIVITY,
        target_doctype=SHORT_REPORT_DOCTYPE,
        target_name=report_id,
        route_type=ROUTE_TYPE_SHORT,
        route_id=report_id,
    )


def _compact(value: str | None, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def _load_short_target(short_id: str | None) -> dict[str, Any] | None:
    short_id = str(short_id or "").strip()
    if not short_id:
        return None
    row = frappe.db.get_value(
        SHORT_DOCTYPE,
        short_id,
        ["name", "caption", "poster_media", "cover_media"],
        as_dict=True,
    )
    if not row:
        return None
    media_id = str(row.cover_media or row.poster_media or "")
    image = MediaService().get_public_url_map([media_id]).get(media_id) if media_id else ""
    return {
        "target_doctype": SHORT_DOCTYPE,
        "target_name": row.name,
        "target_title": _compact(row.caption, 120) or "Short",
        "target_subtitle": "Shorts",
        "target_image": image or "",
        "route_type": ROUTE_TYPE_SHORT,
        "route_id": row.name,
    }


def _safe_record(label: str, fn, *args, **kwargs):
    return best_effort_activity(label, fn, *args, **kwargs)


def record_short_report_activity(
    *,
    user: str | None,
    short_id: str,
    report_id: str,
    reason: str | None = None,
):
    """Record one idempotent private Short-report history item."""
    target = _load_short_target(short_id) if user and report_id else None
    if not target:
        return None
    target["target_subtitle"] = "Reported a short"
    return _safe_record(
        "report",
        ActivityService.record_activity,
        user=user,
        activity_group=SHORT_ACTIVITY_GROUP,
        activity_type=SHORT_REPORT_ACTIVITY,
        metadata={"reason": _compact(reason, 200)},
        unique_key=short_report_unique_key(report_id),
        **target,
    )
