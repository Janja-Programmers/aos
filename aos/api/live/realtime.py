"""
Live Realtime events.

Handles:
- Live lifecycle events
- Viewer count throttling
- Comment events
- Reaction batching
- Display-ready lightweight payloads
"""

from __future__ import annotations

import time
import frappe


# CHANNEL HELPERS
def live_channel(live_id: str) -> str:
    return f"live:{live_id}"


def _live_channel(live_id: str) -> str:
    return live_channel(live_id)


# USER HELPERS
def _get_user_display(user: str | None) -> dict:
    if not user:
        return {
            "user": None,
            "display_name": None,
            "avatar": None,
        }

    row = frappe.db.get_value(
        "User",
        user,
        ["name", "full_name", "user_image"],
        as_dict=True,
    )

    if not row:
        return {
            "user": user,
            "display_name": user,
            "avatar": None,
        }

    return {
        "user": row.name,
        "display_name": row.full_name or row.name,
        "avatar": row.user_image,
    }


def _get_followers(user: str) -> list[str]:
    """
    Return users following this host user.

    AOS Follow is user-to-user:
    - follower_user = the user who follows
    - following_user = the user being followed
    """
    if not user:
        return []

    return frappe.get_all(
        "AOS Follow",
        filters={"following_user": user},
        pluck="follower_user",
    ) or []


def _build_live_payload(live) -> dict:
    host = _get_user_display(live.host_user)

    return {
        "live_id": live.name,
        "id": live.name,
        "host_user": host["user"],
        "host_display_name": host["display_name"],
        "host_avatar": host["avatar"],
        "title": live.title,
        "cover_image": live.cover_image,
        "thumbnail": live.cover_image,
        "viewer_count": int(live.viewer_count or 0),
        "status": live.status,
        "room_name": live.room_name or f"live:{live.name}",
        "started_at": live.started_at,
        "ended_at": live.ended_at,
    }


# INTERNAL CACHE
_VIEWER_CACHE: dict[str, float] = {}
_REACTION_CACHE: dict[str, dict] = {}


# LIFECYCLE EVENTS
def publish_live_started(live):
    """
    Notify followers of the host and the host's own devices.
    """
    payload = _build_live_payload(live)
    host_user = live.host_user

    followers = _get_followers(host_user)

    for user in followers:
        if not user or user == host_user:
            continue

        frappe.publish_realtime(
            event="aos_live_started",
            message=payload,
            user=user,
            after_commit=True,
        )

    # Host multi-device sync.
    if host_user:
        frappe.publish_realtime(
            event="aos_live_started",
            message=payload,
            user=host_user,
            after_commit=True,
        )

    # Optional room/global-style event for clients already listening inside room.
    frappe.publish_realtime(
        event="aos_live_started",
        message=payload,
        room=_live_channel(live.name),
        after_commit=True,
    )


def publish_live_ended(live):
    payload = _build_live_payload(live)

    frappe.publish_realtime(
        event="aos_live_ended",
        message=payload,
        room=_live_channel(live.name),
        after_commit=True,
    )

    if live.host_user:
        frappe.publish_realtime(
            event="aos_live_ended",
            message=payload,
            user=live.host_user,
            after_commit=True,
        )


# VIEWER EVENTS
def publish_viewer_count(live_id: str, viewer_count: int):
    """
    Throttle viewer count updates to max once per second per live.
    """
    now = time.time()
    last_sent = _VIEWER_CACHE.get(live_id)

    if last_sent and (now - last_sent) < 1:
        return

    _VIEWER_CACHE[live_id] = now

    payload = {
        "live_id": live_id,
        "viewer_count": int(viewer_count or 0),
    }

    frappe.publish_realtime(
        event="aos_live_viewer_count",
        message=payload,
        room=_live_channel(live_id),
        after_commit=True,
    )


def publish_viewer_joined(live_id: str, user: str | None = None, session_id: str | None = None):
    user_payload = _get_user_display(user)

    frappe.publish_realtime(
        event="aos_live_viewer_joined",
        message={
            "live_id": live_id,
            "user": user_payload["user"],
            "display_name": user_payload["display_name"],
            "avatar": user_payload["avatar"],
            "session_id": session_id,
            "event": "joined",
        },
        room=_live_channel(live_id),
        after_commit=True,
    )


def publish_viewer_left(live_id: str, user: str | None = None, session_id: str | None = None):
    user_payload = _get_user_display(user)

    frappe.publish_realtime(
        event="aos_live_viewer_left",
        message={
            "live_id": live_id,
            "user": user_payload["user"],
            "display_name": user_payload["display_name"],
            "avatar": user_payload["avatar"],
            "session_id": session_id,
            "event": "left",
        },
        room=_live_channel(live_id),
        after_commit=True,
    )


# COMMENT EVENTS
def publish_comment(live_id: str, comment):
    """
    Comments are low-frequency, so publish immediately with display-ready user info.
    """
    user_payload = _get_user_display(comment.user)

    frappe.publish_realtime(
        event="aos_live_comment",
        message={
            "live_id": live_id,
            "comment": {
                "id": comment.name,
                "user": user_payload["user"],
                "display_name": user_payload["display_name"],
                "avatar": user_payload["avatar"],
                "content": comment.content,
                "status": comment.status,
                "parent_comment": comment.parent_comment,
                "root_comment": comment.root_comment,
                "reply_count": int(comment.reply_count or 0),
                "creation": comment.creation,
            },
        },
        room=_live_channel(live_id),
        after_commit=True,
    )


def publish_comment_deleted(live_id: str, comment_id: str):
    frappe.publish_realtime(
        event="aos_live_comment_deleted",
        message={
            "live_id": live_id,
            "comment_id": comment_id,
        },
        room=_live_channel(live_id),
        after_commit=True,
    )


# REACTION EVENTS
def publish_reaction(live_id: str, reaction_type: str, user: str | None = None):
    """
    Batch reactions and flush every 500ms or every 20 reactions.

    Reactions require login at API/model level, but user remains optional here
    so callers cannot crash realtime if they omit it. When provided, the event
    includes display-ready user context.
    """
    now = time.time()
    user_payload = _get_user_display(user)

    cache = _REACTION_CACHE.setdefault(
        live_id,
        {
            "last_flush": now,
            "items": [],
        },
    )

    cache["items"].append(
        {
            "reaction_type": reaction_type,
            "user": user_payload["user"],
            "display_name": user_payload["display_name"],
            "avatar": user_payload["avatar"],
        }
    )

    if (now - cache["last_flush"] < 0.5) and len(cache["items"]) < 20:
        return

    items = cache["items"]
    cache["items"] = []
    cache["last_flush"] = now

    frappe.publish_realtime(
        event="aos_live_reaction",
        message={
            "live_id": live_id,
            "reactions": items,
        },
        room=_live_channel(live_id),
        after_commit=True,
    )
