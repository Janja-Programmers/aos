"""
Presence APIs (implementation).

Handles:
- send_typing_event realtime

Reusable helpers:
- publish_presence_update
- publish_presence_update_to_peers
- touch_user_activity
"""

from __future__ import annotations
from typing import Any

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
def _as_bool(value: Any) -> bool:
    """
    Safely parse bool-ish values from form/json requests.

    Accepts:
    - true / false
    - 1 / 0
    - "true" / "false"
    - "yes" / "no"
    - "on" / "off"
    """

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value == 1

    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in ("1", "true", "yes", "on")

    return False


def _get_user_summary(user: str) -> dict:
    """
    Return lightweight user display info.

    Fallback is the user id/email only if full_name is unavailable.
    """

    row = frappe.db.get_value(
        "User",
        user,
        ["name", "full_name", "user_image", "last_active"],
        as_dict=True,
    )

    if not row:
        return {
            "user": user,
            "display_name": user,
            "avatar": None,
            "last_active": None,
        }

    return {
        "user": row.name,
        "display_name": row.full_name or row.name,
        "avatar": row.user_image,
        "last_active": row.last_active,
    }


def _get_user_last_active(user: str):
    return frappe.db.get_value("User", user, "last_active")


def _is_online(last_active) -> bool:
    if not last_active:
        return False

    diff = time_diff_in_seconds(now_datetime(), last_active)
    return diff <= ONLINE_THRESHOLD_SECONDS


def _presence_payload(user: str) -> dict:
    """
    Payload consumed by chat/social UI.

    The frontend should display display_name, not user/email.
    """

    summary = _get_user_summary(user)
    last_active = summary.get("last_active")

    return {
        "user": summary["user"],
        "display_name": summary["display_name"],
        "avatar": summary["avatar"],
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

    This keeps presence private/scoped:
    only chat peers receive presence updates.
    """

    rows = frappe.db.sql(
        """
        SELECT DISTINCT
            CASE
                WHEN participant_1 = %(user)s THEN participant_2
                ELSE participant_1
            END AS peer
        FROM `tabAOS Conversation`
        WHERE
            (
                participant_1 = %(user)s
                AND IFNULL(is_active_2, 1) = 1
            )
            OR
            (
                participant_2 = %(user)s
                AND IFNULL(is_active_1, 1) = 1
            )
        """,
        {"user": user},
        as_dict=True,
    )

    peers = {
        row.peer
        for row in rows
        if row.peer and row.peer != user
    }

    return list(peers)


def _should_throttle_presence_publish(user: str) -> bool:
    """
    Prevent flooding presence events.

    Returns True when publish should be skipped.
    """

    cache = frappe.cache()
    key = f"aos:chat:presence:broadcast:{user}"

    if cache.get_value(key):
        return True

    cache.set_value(
        key,
        1,
        expires_in_sec=PRESENCE_BROADCAST_THROTTLE_SECONDS,
    )

    return False


def touch_user_activity(user: str) -> None:
    """
    Update user's last_active timestamp.
    """

    if not user:
        return

    try:
        frappe.db.set_value(
            "User",
            user,
            "last_active",
            now_datetime(),
            update_modified=False,
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Touch User Activity Failed",
        )


# Realtime Presence
def publish_presence_update(user: str, to_user: str | None = None):
    """
    Publish presence update for `user`.

    If `to_user` is provided:
        send only to that user.

    Otherwise:
        broadcast to all relevant active conversation peers.
    """

    if not user:
        return

    payload = _presence_payload(user)

    # Direct push, usually for one peer.
    # Do not throttle direct sends because they are targeted.
    if to_user:
        frappe.publish_realtime(
            event="aos_presence_update",
            message=payload,
            user=to_user,
        )
        return

    # Throttle broadcast to avoid noisy realtime updates.
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
    Call this on user activity:
    - send_message
    - mark_read
    - mark_delivered
    - open conversation
    - list conversations

    It updates last_active first, then publishes presence to peers.
    """

    touch_user_activity(user)
    publish_presence_update(user=user, to_user=None)


# Typing
def send_typing_event_impl(**kwargs):
    """
    Emits realtime typing indicator to the other conversation participant.

    Frontend should auto-clear typing after a short TTL, for example 3–5 seconds,
    in case the "is_typing: false" event is missed.
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
    is_typing = _as_bool(kwargs.get("is_typing"))

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

        # Typing is also user activity.
        touch_user_activity(current_user)

        sender = _get_user_summary(current_user)

        frappe.publish_realtime(
            event="aos_typing",
            message={
                "conversation_id": conv_id,
                "from": sender["user"],
                "from_display_name": sender["display_name"],
                "from_avatar": sender["avatar"],
                "is_typing": is_typing,
            },
            user=receiver,
        )

        # Direct presence update to the receiver so they can immediately
        # see online/last-seen state while chatting.
        publish_presence_update(
            user=current_user,
            to_user=receiver,
        )

        return ok("Typing event sent.")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Typing Event Failed",
        )
        return fail("Failed to send typing event.", code="INTERNAL_ERROR")
