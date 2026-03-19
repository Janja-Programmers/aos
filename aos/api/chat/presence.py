"""
Presence APIs (implementation).

Handles:
- send_typing_event (realtime)

Reusable helpers:
- publish_presence_update
- publish_presence_update_to_peers
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime, time_diff_in_seconds

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import (
    SEND_TYPING_LIMIT_PER_MINUTE_PER_USER,
    PRESENCE_BROADCAST_THROTTLE_SECONDS,
    ONLINE_THRESHOLD_SECONDS,
)


# Helpers
def _get_user_last_active(user: str):
    return frappe.db.get_value("User", user, "last_active")


def _is_online(last_active):
    if not last_active:
        return False

    diff = time_diff_in_seconds(now_datetime(), last_active)
    return diff <= ONLINE_THRESHOLD_SECONDS


def _presence_payload(user: str) -> dict:
    """
    Minimal payload for UI.
    """
    last_active = _get_user_last_active(user)

    return {
        "user": user,
        "is_online": _is_online(last_active),
        "last_seen": last_active,
    }


def _get_conversation_participants(conv_id: str):
    return frappe.db.get_value(
        "AOS Conversation",
        conv_id,
        ["participant_1", "participant_2"],
        as_dict=True,
    )


def _validate_participant(conv_row, user: str) -> bool:
    if not conv_row:
        return False
    return user in (conv_row.participant_1, conv_row.participant_2)


def _get_other_participant(conv_row, user: str) -> str | None:
    if not conv_row:
        return None
    if conv_row.participant_1 == user:
        return conv_row.participant_2
    if conv_row.participant_2 == user:
        return conv_row.participant_1
    return None


def _get_presence_subscribers_for_user(user: str) -> list[str]:
    """
    Return distinct users who share an active conversation with `user`.

    Optimized: only returns peers who should actually receive updates.
    """
    rows_1 = frappe.get_all(
        "AOS Conversation",
        filters={
            "participant_1": user,
            "is_active_2": 1,
        },
        fields=["participant_2 as peer"],
        limit_page_length=1000,
    )

    rows_2 = frappe.get_all(
        "AOS Conversation",
        filters={
            "participant_2": user,
            "is_active_1": 1,
        },
        fields=["participant_1 as peer"],
        limit_page_length=1000,
    )

    peers = {r.peer for r in rows_1 + rows_2 if r.peer and r.peer != user}
    return list(peers)


def _should_throttle_presence_publish(user: str) -> bool:
    """
    Prevent flooding presence events.
    """
    cache = frappe.cache()
    key = f"aos:chat:presence:broadcast:{user}"

    if cache.get_value(key):
        return True

    cache.set_value(key, 1, expires_in_sec=PRESENCE_BROADCAST_THROTTLE_SECONDS)
    return False


# Realtime Presence
def publish_presence_update(user: str, to_user: str | None = None):
    """
    Publish presence update for `user`.

    If `to_user` is provided → send only to that user
    Otherwise → broadcast to all relevant peers
    """
    payload = _presence_payload(user)

    # Direct push (used rarely)
    if to_user:
        frappe.publish_realtime(
            event="aos_presence_update",
            message=payload,
            user=to_user,
        )
        return

    # Throttle broadcast
    if _should_throttle_presence_publish(user):
        return

    peers = _get_presence_subscribers_for_user(user)

    for peer in peers:
        frappe.publish_realtime(
            event="aos_presence_update",
            message=payload,
            user=peer,
        )


def publish_presence_update_to_peers(user: str):
    """
    Call this on ANY user activity:
    - send_message
    - mark_read
    - mark_delivered
    - open conversation
    - list conversations
    """
    publish_presence_update(user=user, to_user=None)


# Typing
def send_typing_event_impl(**kwargs):
    """
    Emits realtime typing indicator to other participant.
    """
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:typing:user:{current_user}",
        ttl_seconds=60,
        limit=SEND_TYPING_LIMIT_PER_MINUTE_PER_USER,
        message="Too many typing events. Please slow down.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")
    is_typing = int(kwargs.get("is_typing") or 0)

    if not conv_id:
        return fail("conversation_id is required.", code="VALIDATION_ERROR")

    try:
        conv_row = _get_conversation_participants(conv_id)
        if not conv_row:
            return fail("Conversation not found.", code="NOT_FOUND")

        if not _validate_participant(conv_row, current_user):
            return fail("Not allowed.", code="PERMISSION_DENIED")

        receiver = _get_other_participant(conv_row, current_user)
        if not receiver:
            return fail("Receiver not found.", code="NOT_FOUND")

        frappe.publish_realtime(
            event="aos_typing",
            message={
                "conversation_id": conv_id,
                "from": current_user,
                "is_typing": bool(is_typing),
            },
            user=receiver,
        )

        return ok("Typing event sent.")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Typing Event Failed",
        )
        return fail("Failed to send typing event.", code="INTERNAL_ERROR")
