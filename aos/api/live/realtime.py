"""
Live Realtime events.

Handles:
- Viewer count throttled
- Reactions batched
- Lightweight payloads
"""

from __future__ import annotations

import time
import frappe


# CHANNEL HELPERS
def _live_channel(live_id: str) -> str:
    return f"aos_live:{live_id}"


def _seller_channel(user: str) -> str:
    return f"user:{user}"


# INTERNAL CACHE (THROTTLING)
_VIEWER_CACHE = {}
_REACTION_CACHE = {}

# LIFECYCLE EVENTS
def publish_live_started(live):
    frappe.publish_realtime(
        event="aos_live_started",
        message={
            "live_id": live.name,
            "seller": live.seller,
            "title": live.title,
        },
        after_commit=True,
    )


def publish_live_ended(live):
    frappe.publish_realtime(
        event="aos_live_ended",
        message={"live_id": live.name},
        room=_live_channel(live.name),
        after_commit=True,
    )


# VIEWER EVENTS
def publish_viewer_count(live_id: str, viewer_count: int):
    """
    Throttle viewer updates (max 1 per second per live).
    """
    now = time.time()

    last_sent = _VIEWER_CACHE.get(live_id)

    # send only if 1 second passed
    if last_sent and (now - last_sent) < 1:
        return

    _VIEWER_CACHE[live_id] = now

    frappe.publish_realtime(
        event="aos_live_viewers",
        message={
            "live_id": live_id,
            "viewer_count": viewer_count,
        },
        room=_live_channel(live_id),
        after_commit=True,
    )


# COMMENT EVENTS
def publish_comment(live_id: str, comment):
    """
    Comments are low-frequency → send immediately.
    """
    frappe.publish_realtime(
        event="aos_live_comment",
        message={
            "live_id": live_id,
            "comment": {
                "id": comment.name,
                "user": comment.user,
                "seller": comment.seller,
                "content": comment.content,
                "parent_comment": comment.parent_comment,
                "creation": comment.creation,
            },
        },
        room=_live_channel(live_id),
        after_commit=True,
    )


# REACTION EVENTS
def publish_reaction(live_id: str, reaction_type: str):
    """
    Batch reactions and flush every 500ms.
    """
    now = time.time()

    cache = _REACTION_CACHE.setdefault(live_id, {
        "last_flush": now,
        "items": []
    })

    cache["items"].append(reaction_type)

    # flush every 0.5 sec OR if too many
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


# AD EVENTS
def publish_pinned_ad(live_id: str, ad_id: str):
    frappe.publish_realtime(
        event="aos_live_pinned_ad",
        message={
            "live_id": live_id,
            "ad_id": ad_id,
        },
        room=_live_channel(live_id),
        after_commit=True,
    )
