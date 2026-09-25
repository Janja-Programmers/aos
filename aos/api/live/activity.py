"""Private Activity Center hooks for canonical Live mutations.

Live remains authoritative for stream/message lifecycle and visibility.  These
helpers only emit bounded history after Live has accepted the mutation.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.accounts.identity import public_account_id_for_user
from aos.services.activity.producer import best_effort_activity
from aos.services.activity_service import ActivityService

LIVE_STREAM_DOCTYPE = "AOS Live Stream"
LIVE_MESSAGE_DOCTYPE = "AOS Live Message"
LIVE_ACTIVITY_GROUP = "Live"

LIVE_HOST_ACTIVITY = "live_host"
LIVE_JOIN_ACTIVITY = "live_join"
LIVE_COMMENT_ACTIVITY = "live_comment"
ROUTE_TYPE_LIVE = "live"


def _compact_text(value: str | None, *, max_len: int = 120) -> str:
    value = " ".join((value or "").strip().split())
    if not value:
        return ""
    if len(value) <= max_len:
        return value
    return value[: max_len - 1].rstrip() + "…"


def _safe_record(action_name: str, fn, *args, **kwargs) -> str | bool | None:
    return best_effort_activity(action_name, fn, *args, **kwargs)


def live_host_unique_key(live_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=LIVE_HOST_ACTIVITY,
        target_doctype=LIVE_STREAM_DOCTYPE,
        target_name=live_id,
        route_type=ROUTE_TYPE_LIVE,
        route_id=live_id,
    )


def live_join_unique_key(live_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=LIVE_JOIN_ACTIVITY,
        target_doctype=LIVE_STREAM_DOCTYPE,
        target_name=live_id,
        route_type=ROUTE_TYPE_LIVE,
        route_id=live_id,
    )


def live_comment_unique_key(message_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=LIVE_COMMENT_ACTIVITY,
        target_doctype=LIVE_MESSAGE_DOCTYPE,
        target_name=message_id,
        route_type=ROUTE_TYPE_LIVE,
        route_id=message_id,
    )


def _load_live_target(live_id: str | None) -> dict[str, Any] | None:
    live_id = (live_id or "").strip()
    if not live_id:
        return None
    live = frappe.db.get_value(
        LIVE_STREAM_DOCTYPE,
        live_id,
        ["name", "title", "host_user", "cover_image"],
        as_dict=True,
    )
    if not live:
        return None
    host_public_id = public_account_id_for_user(live.host_user)
    if not host_public_id:
        return None
    return {
        "target_doctype": LIVE_STREAM_DOCTYPE,
        "target_name": live.name,
        "target_title": _compact_text(live.title, max_len=120) or "Live stream",
        "target_subtitle": "Live",
        "target_image": live.cover_image or "",
        "route_type": ROUTE_TYPE_LIVE,
        "route_id": live.name,
        "metadata": {"live_id": live.name, "host_user": host_public_id},
    }


def record_live_host_activity(*, user: str | None, live_id: str) -> str | None:
    """Record hosting a stream once; Live activation is the authority."""
    if not user:
        return None
    target = _load_live_target(live_id)
    if not target:
        return None
    metadata = target.pop("metadata", None)
    return _safe_record(
        "record_live_host_activity",
        ActivityService.record_activity,
        user=user,
        activity_group=LIVE_ACTIVITY_GROUP,
        activity_type=LIVE_HOST_ACTIVITY,
        metadata=metadata,
        unique_key=live_host_unique_key(live_id),
        **target,
    )


def record_live_join_activity(
    *,
    user: str | None,
    live_id: str,
    session_id: str | None = None,
    view_id: str | None = None,
) -> str | None:
    """Coalesce a private joined-live history item.

    Session/view ids are accepted from the owning Live path for call-site
    stability but are analytics internals and are never persisted in Activity.
    """
    del session_id, view_id
    if not user:
        return None
    target = _load_live_target(live_id)
    if not target:
        return None
    metadata = target.pop("metadata", None)
    return _safe_record(
        "record_live_join_activity",
        ActivityService.record_or_update_activity,
        user=user,
        activity_group=LIVE_ACTIVITY_GROUP,
        activity_type=LIVE_JOIN_ACTIVITY,
        metadata=metadata,
        unique_key=live_join_unique_key(live_id),
        **target,
    )


def record_live_comment_activity(
    *,
    user: str | None,
    live_id: str,
    message_id: str,
    content: str | None = None,
    parent_message_id: str | None = None,
) -> str | None:
    """Record one idempotent live comment/reply history item."""
    if not user or not message_id:
        return None
    target = _load_live_target(live_id)
    if not target:
        return None
    metadata = target.pop("metadata", None) or {}
    metadata["is_reply"] = bool(parent_message_id)
    if content is not None:
        metadata["comment_preview"] = _compact_text(content, max_len=160)
    return _safe_record(
        "record_live_comment_activity",
        ActivityService.record_activity,
        user=user,
        activity_group=LIVE_ACTIVITY_GROUP,
        activity_type=LIVE_COMMENT_ACTIVITY,
        target_doctype=LIVE_MESSAGE_DOCTYPE,
        target_name=message_id,
        target_title=target.get("target_title"),
        target_subtitle="Live comment",
        target_image=target.get("target_image"),
        route_type=ROUTE_TYPE_LIVE,
        route_id=live_id,
        metadata=metadata,
        unique_key=live_comment_unique_key(message_id),
    )


def hide_live_comment_activity(*, user: str | None, message_id: str) -> bool:
    """Hide a live-comment item when the owning Live message is deleted."""
    if not user or not message_id:
        return False
    return bool(
        _safe_record(
            "hide_live_comment_activity",
            ActivityService.hide_activity_by_unique_key,
            user=user,
            unique_key=live_comment_unique_key(message_id),
        )
    )
