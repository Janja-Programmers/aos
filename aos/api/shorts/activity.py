"""Activity Center hooks for Shorts.

This module keeps Activity Center payload creation out of the core Shorts
endpoints. Shorts doctypes remain the source of truth; AOS User Activity is the
private user-facing history layer.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.sellers.identity import public_seller_id_for_name

from aos.services.activity_service import ActivityService

SHORT_DOCTYPE = "AOS Short"
SHORT_COMMENT_DOCTYPE = "AOS Short Comment"
SHORT_ACTIVITY_GROUP = "Shorts"

SHORT_WATCH_ACTIVITY = "short_watch"
SHORT_LIKE_ACTIVITY = "short_like"
SHORT_COMMENT_ACTIVITY = "short_comment"
SHORT_REPORT_ACTIVITY = "short_report"
SHORT_REPOST_ACTIVITY = "short_repost"

ROUTE_TYPE_SHORT = "short"


# UNIQUE KEYS
def short_watch_unique_key(short_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=SHORT_WATCH_ACTIVITY,
        target_doctype=SHORT_DOCTYPE,
        target_name=short_id,
        route_type=ROUTE_TYPE_SHORT,
        route_id=short_id,
    )


def short_like_unique_key(short_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=SHORT_LIKE_ACTIVITY,
        target_doctype=SHORT_DOCTYPE,
        target_name=short_id,
        route_type=ROUTE_TYPE_SHORT,
        route_id=short_id,
    )


def short_comment_unique_key(comment_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=SHORT_COMMENT_ACTIVITY,
        target_doctype=SHORT_COMMENT_DOCTYPE,
        target_name=comment_id,
        route_type=ROUTE_TYPE_SHORT,
        route_id=comment_id,
    )


def short_repost_unique_key(short_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=SHORT_REPOST_ACTIVITY,
        target_doctype=SHORT_DOCTYPE,
        target_name=short_id,
        route_type=ROUTE_TYPE_SHORT,
        route_id=short_id,
    )


def short_report_unique_key(report_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=SHORT_REPORT_ACTIVITY,
        target_doctype="AOS Short Report",
        target_name=report_id,
        route_type=ROUTE_TYPE_SHORT,
        route_id=report_id,
    )


# TARGET LOADING
def _compact_text(value: str | None, *, max_len: int = 120) -> str:
    value = " ".join((value or "").strip().split())
    if not value:
        return ""

    if len(value) <= max_len:
        return value

    return value[: max_len - 1].rstrip() + "…"


def _load_short_target(short_id: str | None) -> dict[str, Any] | None:
    short_id = (short_id or "").strip()
    if not short_id:
        return None

    short = frappe.db.get_value(
        SHORT_DOCTYPE,
        short_id,
        [
            "name",
            "caption",
            "thumbnail_url",
            "owner",
            "seller",
            "ad",
            "status",
            "visibility_status",
        ],
        as_dict=True,
    )

    if not short:
        return None

    caption = _compact_text(short.caption, max_len=120)
    title = caption or "Short"

    return {
        "target_doctype": SHORT_DOCTYPE,
        "target_name": short.name,
        "target_title": title,
        "target_subtitle": "Shorts",
        "target_image": short.thumbnail_url or "",
        "route_type": ROUTE_TYPE_SHORT,
        "route_id": short.name,
        "metadata": {
            "short_owner": short.owner,
            "seller": public_seller_id_for_name(short.seller),
            "ad": short.ad,
            "short_status": short.status,
            "visibility_status": short.visibility_status,
        },
    }


def _merge_metadata(base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if base:
        merged.update(base)
    if extra:
        merged.update(extra)
    return merged


# RECORDING HOOKS
def record_short_watch_activity(
    *,
    user: str | None,
    short_id: str,
    watch_ms: int | None = None,
) -> str | None:
    """Record/de-dupe a user's Shorts watch history item."""
    if not user:
        return None

    target = _load_short_target(short_id)
    if not target:
        return None

    metadata = _merge_metadata(
        target.pop("metadata", None),
        {"watch_ms": int(watch_ms or 0)},
    )

    return ActivityService.record_or_update_activity(
        user=user,
        activity_group=SHORT_ACTIVITY_GROUP,
        activity_type=SHORT_WATCH_ACTIVITY,
        metadata=metadata,
        unique_key=short_watch_unique_key(short_id),
        **target,
    )


def record_short_like_activity(
    *,
    user: str | None,
    short_id: str,
) -> str | None:
    """Record/de-dupe a Shorts like history item."""
    if not user:
        return None

    target = _load_short_target(short_id)
    if not target:
        return None

    metadata = target.pop("metadata", None)

    return ActivityService.record_or_update_activity(
        user=user,
        activity_group=SHORT_ACTIVITY_GROUP,
        activity_type=SHORT_LIKE_ACTIVITY,
        metadata=metadata,
        unique_key=short_like_unique_key(short_id),
        **target,
    )


def hide_short_like_activity(
    *,
    user: str | None,
    short_id: str,
) -> bool:
    """Hide a Shorts like history item after unlike."""
    if not user:
        return False

    return ActivityService.hide_activity_by_unique_key(
        user=user,
        unique_key=short_like_unique_key(short_id),
    )


def record_short_comment_activity(
    *,
    user: str | None,
    short_id: str,
    comment_id: str,
    comment_text: str | None = None,
    parent_comment_id: str | None = None,
) -> str | None:
    """Record one Shorts comment/reply history item."""
    if not user or not comment_id:
        return None

    target = _load_short_target(short_id)
    if not target:
        return None

    metadata = _merge_metadata(
        target.pop("metadata", None),
        {
            "comment_id": comment_id,
            "parent_comment_id": parent_comment_id,
            "is_reply": bool(parent_comment_id),
        },
    )

    if comment_text is not None:
        metadata["comment_preview"] = _compact_text(comment_text, max_len=160)

    target["target_subtitle"] = _compact_text(comment_text, max_len=160) or "Commented on a short"

    return ActivityService.record_activity(
        user=user,
        activity_group=SHORT_ACTIVITY_GROUP,
        activity_type=SHORT_COMMENT_ACTIVITY,
        metadata=metadata,
        unique_key=short_comment_unique_key(comment_id),
        **target,
    )


def hide_short_comment_activity(
    *,
    user: str | None,
    comment_id: str,
) -> bool:
    """Hide a Shorts comment history item after the source comment is deleted."""
    if not user or not comment_id:
        return False

    return ActivityService.hide_activity_by_unique_key(
        user=user,
        unique_key=short_comment_unique_key(comment_id),
    )


def record_short_report_activity(
    *,
    user: str | None,
    short_id: str,
    report_id: str,
    reason: str | None = None,
) -> str | None:
    """Record a private Activity Center row for a submitted short report."""
    if not user or not report_id:
        return None

    target = _load_short_target(short_id)
    if not target:
        return None

    metadata = _merge_metadata(
        target.pop("metadata", None),
        {"report_id": report_id, "reason": reason},
    )

    target["target_subtitle"] = "Reported a short"

    return ActivityService.record_activity(
        user=user,
        activity_group=SHORT_ACTIVITY_GROUP,
        activity_type=SHORT_REPORT_ACTIVITY,
        metadata=metadata,
        unique_key=short_report_unique_key(report_id),
        **target,
    )


def record_short_repost_activity(
    *,
    user: str | None,
    short_id: str,
) -> str | None:
    """Record/de-dupe a Shorts repost history item."""
    if not user:
        return None

    target = _load_short_target(short_id)
    if not target:
        return None

    metadata = target.pop("metadata", None)
    target["target_subtitle"] = "Reposted a short"

    return ActivityService.record_or_update_activity(
        user=user,
        activity_group=SHORT_ACTIVITY_GROUP,
        activity_type=SHORT_REPOST_ACTIVITY,
        metadata=metadata,
        unique_key=short_repost_unique_key(short_id),
        **target,
    )


def hide_short_repost_activity(
    *,
    user: str | None,
    short_id: str,
) -> bool:
    """Hide a Shorts repost history item after repost removal."""
    if not user:
        return False

    return ActivityService.hide_activity_by_unique_key(
        user=user,
        unique_key=short_repost_unique_key(short_id),
    )
