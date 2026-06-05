"""
Live realtime events.

Handles:
- Live lifecycle events
- Viewer count events
- Unified live message events
- User-targeted live message events
- Reaction batching
- Display-ready lightweight payloads

Privacy:
- Viewer identities and session IDs are not broadcast to the live room.
- Room participants receive viewer-count changes only.
- Host-only activity is delivered through targeted live messages.
"""

from __future__ import annotations

import time
from typing import Any
import frappe


# INTERNAL CACHE
_VIEWER_CACHE: dict[str, float] = {}
_REACTION_CACHE: dict[str, dict[str, Any]] = {}


# CHANNEL HELPERS
def live_channel(live_id: str) -> str:
    return f"live:{live_id}"


# USER HELPERS
def _get_user_display(
    user: str | None,
) -> dict:
    if not user:
        return {
            "user": None,
            "display_name": None,
            "avatar": None,
        }

    row = frappe.db.get_value(
        "User",
        user,
        [
            "name",
            "full_name",
            "user_image",
        ],
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
        "display_name": (
            row.full_name
            or row.name
        ),
        "avatar": row.user_image,
    }


def _get_followers(
    user: str,
) -> list[str]:
    """
    Return users following the supplied host.

    AOS Follow uses:
    - follower_user: user who follows
    - following_user: user being followed
    """
    if not user:
        return []

    return (
        frappe.get_all(
            "AOS Follow",
            filters={
                "following_user": user,
            },
            pluck="follower_user",
        )
        or []
    )


# PAYLOAD HELPERS
def _build_live_payload(live) -> dict:
    host = _get_user_display(
        live.host_user
    )

    return {
        "live_id": live.name,
        "id": live.name,
        "host_user": host["user"],
        "host_display_name": host[
            "display_name"
        ],
        "host_avatar": host["avatar"],
        "title": live.title,
        "cover_image": live.cover_image,
        "thumbnail": live.cover_image,
        "viewer_count": int(
            live.viewer_count or 0
        ),
        "status": live.status,
        "room_name": (
            live.room_name
            or f"live:{live.name}"
        ),
        "started_at": live.started_at,
        "ended_at": live.ended_at,
    }


def _build_live_message_payload(
    *,
    live_id: str,
    message: dict,
) -> dict:
    """
    Build the canonical realtime envelope for a live message.
    """

    return {
        "live_id": live_id,
        "message": message,
    }


# LIFECYCLE EVENTS
def publish_live_started(live):
    """
    Notify:
    - Followers of the host
    - The host's active devices
    - Clients already subscribed to the live room
    """
    payload = _build_live_payload(live)
    host_user = live.host_user

    followers = _get_followers(
        host_user
    )

    for user in followers:
        if not user or user == host_user:
            continue

        frappe.publish_realtime(
            event="aos_live_started",
            message=payload,
            user=user,
            after_commit=True,
        )

    if host_user:
        frappe.publish_realtime(
            event="aos_live_started",
            message=payload,
            user=host_user,
            after_commit=True,
        )

    frappe.publish_realtime(
        event="aos_live_started",
        message=payload,
        room=live_channel(live.name),
        after_commit=True,
    )


def publish_live_ended(live):
    payload = _build_live_payload(live)

    frappe.publish_realtime(
        event="aos_live_ended",
        message=payload,
        room=live_channel(live.name),
        after_commit=True,
    )

    if live.host_user:
        frappe.publish_realtime(
            event="aos_live_ended",
            message=payload,
            user=live.host_user,
            after_commit=True,
        )


# VIEWER COUNT EVENTS
def publish_viewer_count(
    live_id: str,
    viewer_count: int,
):
    """
    Publish the current viewer count to the live room.

    Updates are throttled to at most once per second per live stream.
    Viewer identities and session IDs are never included.
    """

    now = time.time()

    last_sent = _VIEWER_CACHE.get(
        live_id
    )

    if (
        last_sent
        and (now - last_sent) < 1
    ):
        return

    _VIEWER_CACHE[live_id] = now

    frappe.publish_realtime(
        event="aos_live_viewer_count",
        message={
            "live_id": live_id,
            "viewer_count": int(
                viewer_count or 0
            ),
        },
        room=live_channel(live_id),
        after_commit=True,
    )


# LIVE MESSAGE EVENTS
def publish_live_message(
    live_id: str,
    message: dict,
):
    """
    Publish a live message to every client subscribed to the live room.

    Use only for messages visible to room participants.

    Examples:
    - Viewer comments
    - Replies
    - Public lifecycle messages
    - Public co-host events
    """

    frappe.publish_realtime(
        event="aos_live_message",
        message=_build_live_message_payload(
            live_id=live_id,
            message=message,
        ),
        room=live_channel(live_id),
        after_commit=True,
    )


def publish_live_message_to_user(
    *,
    user: str,
    live_id: str,
    message: dict,
):
    """
    Publish a live message only to one user's active devices.

    Use for:
    - Host-only startup messages
    - Host-only viewer-joined messages
    - Co-host invitations
    - Co-host requests delivered only to the host
    """

    if not user:
        return

    frappe.publish_realtime(
        event="aos_live_message",
        message=_build_live_message_payload(
            live_id=live_id,
            message=message,
        ),
        user=user,
        after_commit=True,
    )


def publish_live_message_to_users(
    *,
    users: list[str],
    live_id: str,
    message: dict,
):
    """
    Publish the same message to several specific users.

    Empty and duplicate recipients are removed.
    """

    recipients = {
        user
        for user in users
        if user
    }

    for user in recipients:
        publish_live_message_to_user(
            user=user,
            live_id=live_id,
            message=message,
        )


def publish_live_message_deleted(
    live_id: str,
    message_id: str,
    *,
    deleted_message_ids: list[str] | None = None,
):
    """
    Notify room participants that messages were soft-deleted.

    deleted_message_ids contains the selected message and any descendant
    replies deleted with it.
    """

    ids = list(
        dict.fromkeys(
            deleted_message_ids
            or [message_id]
        )
    )

    if message_id not in ids:
        ids.insert(0, message_id)

    frappe.publish_realtime(
        event="aos_live_message_deleted",
        message={
            "live_id": live_id,
            "message_id": message_id,
            "deleted_message_ids": ids,
        },
        room=live_channel(live_id),
        after_commit=True,
    )


# REACTION EVENTS
def publish_reaction(
    live_id: str,
    reaction_type: str,
    user: str | None = None,
):
    """
    Batch reactions and flush when:
    - At least 500 milliseconds have passed since the previous flush, or
    - The batch reaches 20 reactions.

    Reaction events may include public user display context, but never
    include private session identifiers.
    """
    now = time.time()

    user_payload = _get_user_display(
        user
    )

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
            "display_name": user_payload[
                "display_name"
            ],
            "avatar": user_payload["avatar"],
        }
    )

    should_flush_by_time = (
        now
        - float(cache["last_flush"])
    ) >= 0.5

    should_flush_by_size = (
        len(cache["items"]) >= 20
    )

    if (
        not should_flush_by_time
        and not should_flush_by_size
    ):
        return

    items = list(
        cache["items"]
    )

    cache["items"] = []
    cache["last_flush"] = now

    frappe.publish_realtime(
        event="aos_live_reaction",
        message={
            "live_id": live_id,
            "reactions": items,
        },
        room=live_channel(live_id),
        after_commit=True,
    )


# CACHE CLEANUP
def clear_live_realtime_cache(
    live_id: str,
):
    """
    Clear process-local throttling and batching state after a live ends.
    """

    _VIEWER_CACHE.pop(
        live_id,
        None,
    )

    _REACTION_CACHE.pop(
        live_id,
        None,
    )
