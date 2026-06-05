"""
Live realtime events.

Handles:
- Live lifecycle events
- Viewer count events
- Unified live message events
- User-targeted live message events
- Immediate reaction events
- Display-ready lightweight payloads

Privacy:
- Viewer identities and session IDs are not broadcast to the live room.
- Room participants receive viewer-count changes only.
- Host-only activity is delivered through targeted live messages.
"""

from __future__ import annotations

import frappe


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
    payload = _build_live_payload(
        live
    )

    host_user = live.host_user

    for user in _get_followers(
        host_user
    ):
        if (
            not user
            or user == host_user
        ):
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
    payload = _build_live_payload(
        live
    )

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
    Publish the current viewer count immediately.

    Viewer identities and session IDs are never included.
    """
    if not live_id:
        return

    frappe.publish_realtime(
        event="aos_live_viewer_count",
        message={
            "live_id": live_id,
            "viewer_count": max(
                int(viewer_count or 0),
                0,
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
    Publish a message to every client subscribed to the live room.

    Use only for messages visible to room participants.
    """
    if not live_id or not message:
        return

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

    if (
        not user
        or not live_id
        or not message
    ):
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
    if not live_id or not message:
        return

    for user in {
        user
        for user in users
        if user
    }:
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
    if not live_id or not message_id:
        return

    ids = list(
        dict.fromkeys(
            deleted_message_ids
            or [message_id]
        )
    )

    if message_id not in ids:
        ids.insert(
            0,
            message_id,
        )

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
    *,
    live_id: str,
    reaction: dict,
):
    """
    Publish one canonical reaction immediately.

    The API response and realtime event use the same serialized payload.
    """
    if not live_id or not reaction:
        return

    frappe.publish_realtime(
        event="aos_live_reaction",
        message={
            "live_id": live_id,
            "reaction": reaction,
        },
        room=live_channel(live_id),
        after_commit=True,
    )
