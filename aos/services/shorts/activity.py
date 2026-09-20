"""Activity integration for Shorts.

Activity remains a separate domain.  Shorts only emits compact snapshots through
its existing service and never stores a duplicate activity model.
"""
from __future__ import annotations

from typing import Any

import frappe

from aos.services.accounts.identity import public_account_id_for_user
from aos.services.activity_service import ActivityService
from aos.services.media.media_service import MediaService

SHORT_DOCTYPE = "AOS Short"
SHORT_COMMENT_DOCTYPE = "AOS Short Comment"
SHORT_ACTIVITY_GROUP = "Shorts"
ROUTE_TYPE_SHORT = "short"

SHORT_WATCH_ACTIVITY = "short_watch"
SHORT_LIKE_ACTIVITY = "short_like"
SHORT_COMMENT_ACTIVITY = "short_comment"
SHORT_REPORT_ACTIVITY = "short_report"
SHORT_REPOST_ACTIVITY = "short_repost"


def _unique(activity_type: str, doctype: str, name: str, route_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=activity_type,
        target_doctype=doctype,
        target_name=name,
        route_type=ROUTE_TYPE_SHORT,
        route_id=route_id,
    )


def short_watch_unique_key(short_id: str) -> str:
    return _unique(SHORT_WATCH_ACTIVITY, SHORT_DOCTYPE, short_id, short_id)


def short_like_unique_key(short_id: str) -> str:
    return _unique(SHORT_LIKE_ACTIVITY, SHORT_DOCTYPE, short_id, short_id)


def short_comment_unique_key(comment_id: str) -> str:
    return _unique(SHORT_COMMENT_ACTIVITY, SHORT_COMMENT_DOCTYPE, comment_id, comment_id)


def short_report_unique_key(report_id: str) -> str:
    return _unique(SHORT_REPORT_ACTIVITY, "AOS Short Report", report_id, report_id)


def short_repost_unique_key(short_id: str) -> str:
    return _unique(SHORT_REPOST_ACTIVITY, SHORT_DOCTYPE, short_id, short_id)


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
        ["name", "caption", "owner", "poster_media", "cover_media", "lifecycle_status", "moderation_status"],
        as_dict=True,
    )
    if not row:
        return None
    media_id = str(row.cover_media or row.poster_media or "")
    image = MediaService().get_public_url_map([media_id]).get(media_id) if media_id else ""
    ad_ids = [
        str(r.public_id)
        for r in frappe.db.sql(
            """SELECT a.public_id FROM `tabAOS Short Ad` sa
                 INNER JOIN `tabAOS Ad` a ON a.name=sa.ad
                WHERE sa.short=%s AND a.public_id IS NOT NULL
                ORDER BY sa.position,sa.name LIMIT 8""",
            (short_id,),
            as_dict=True,
        )
    ]
    return {
        "target_doctype": SHORT_DOCTYPE,
        "target_name": row.name,
        "target_title": _compact(row.caption, 120) or "Short",
        "target_subtitle": "Shorts",
        "target_image": image or "",
        "route_type": ROUTE_TYPE_SHORT,
        "route_id": row.name,
        "metadata": {
            "short_owner": public_account_id_for_user(row.owner),
            "ad_ids": ad_ids,
            "lifecycle_status": row.lifecycle_status,
            "moderation_status": row.moderation_status,
        },
    }


def _safe_record(label: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"AOS Shorts Activity Hook Failed: {label}")
        return None


def record_short_watch_activity(*, user: str | None, short_id: str, watch_ms: int | None = None):
    target = _load_short_target(short_id) if user else None
    if not target:
        return None
    metadata = dict(target.pop("metadata", {}) or {})
    metadata["watch_ms"] = max(0, int(watch_ms or 0))
    return _safe_record(
        "watch", ActivityService.record_or_update_activity,
        user=user, activity_group=SHORT_ACTIVITY_GROUP, activity_type=SHORT_WATCH_ACTIVITY,
        metadata=metadata, unique_key=short_watch_unique_key(short_id), **target,
    )


def record_short_like_activity(*, user: str | None, short_id: str):
    target = _load_short_target(short_id) if user else None
    if not target:
        return None
    return _safe_record(
        "like", ActivityService.record_or_update_activity,
        user=user, activity_group=SHORT_ACTIVITY_GROUP, activity_type=SHORT_LIKE_ACTIVITY,
        metadata=target.pop("metadata", None), unique_key=short_like_unique_key(short_id), **target,
    )


def hide_short_like_activity(*, user: str | None, short_id: str) -> bool:
    return bool(user and _safe_record("unlike", ActivityService.hide_activity_by_unique_key, user=user, unique_key=short_like_unique_key(short_id)))


def record_short_comment_activity(*, user: str | None, short_id: str, comment_id: str, comment_text: str | None = None, parent_comment_id: str | None = None):
    target = _load_short_target(short_id) if user and comment_id else None
    if not target:
        return None
    metadata = dict(target.pop("metadata", {}) or {})
    metadata.update({"comment_id": comment_id, "parent_comment_id": parent_comment_id, "is_reply": bool(parent_comment_id)})
    if comment_text is not None:
        metadata["comment_preview"] = _compact(comment_text, 160)
    target["target_subtitle"] = _compact(comment_text, 160) or "Commented on a short"
    return _safe_record(
        "comment", ActivityService.record_activity,
        user=user, activity_group=SHORT_ACTIVITY_GROUP, activity_type=SHORT_COMMENT_ACTIVITY,
        metadata=metadata, unique_key=short_comment_unique_key(comment_id), **target,
    )


def hide_short_comment_activity(*, user: str | None, comment_id: str) -> bool:
    return bool(user and comment_id and _safe_record("comment_delete", ActivityService.hide_activity_by_unique_key, user=user, unique_key=short_comment_unique_key(comment_id)))


def record_short_report_activity(*, user: str | None, short_id: str, report_id: str, reason: str | None = None):
    target = _load_short_target(short_id) if user and report_id else None
    if not target:
        return None
    metadata = dict(target.pop("metadata", {}) or {})
    metadata.update({"report_id": report_id, "reason": reason})
    target["target_subtitle"] = "Reported a short"
    return _safe_record(
        "report", ActivityService.record_activity,
        user=user, activity_group=SHORT_ACTIVITY_GROUP, activity_type=SHORT_REPORT_ACTIVITY,
        metadata=metadata, unique_key=short_report_unique_key(report_id), **target,
    )


def record_short_repost_activity(*, user: str | None, short_id: str):
    target = _load_short_target(short_id) if user else None
    if not target:
        return None
    target["target_subtitle"] = "Reposted a short"
    return _safe_record(
        "repost", ActivityService.record_or_update_activity,
        user=user, activity_group=SHORT_ACTIVITY_GROUP, activity_type=SHORT_REPOST_ACTIVITY,
        metadata=target.pop("metadata", None), unique_key=short_repost_unique_key(short_id), **target,
    )


def hide_short_repost_activity(*, user: str | None, short_id: str) -> bool:
    return bool(user and _safe_record("undo_repost", ActivityService.hide_activity_by_unique_key, user=user, unique_key=short_repost_unique_key(short_id)))
