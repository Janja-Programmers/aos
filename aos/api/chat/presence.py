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
from aos.api.shared.blocking import get_blocked_user_set
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.api.shared.user_display import get_user_display

from .constants import (
    SEND_TYPING_LIMIT_PER_MINUTE_PER_USER,
    GET_PRESENCE_LIMIT_PER_MINUTE_PER_USER,
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
    """Return display-safe user summary plus presence timestamp."""

    display = get_user_display(user)
    display["last_active"] = frappe.db.get_value("User", user, "last_active")
    return display


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
    unavailable = bool(summary.get("is_deleted"))
    last_active = None if unavailable else summary.get("last_active")

    return {
        "account_id": summary["account_id"],
        "display_name": summary["display_name"],
        "avatar": summary["avatar"],
        "is_live": bool(summary.get("is_live")) if not unavailable else False,
        "live_id": summary.get("live_id") if not unavailable else None,
        "live_status": summary.get("live_status") if not unavailable else None,
        "is_online": _is_online(last_active) if not unavailable else False,
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

    peers = {row.peer for row in rows if row.peer and row.peer != user}
    if not peers:
        return []
    blocked = get_blocked_user_set(user, peers)
    return sorted(peer for peer in peers if peer not in blocked)


def _should_throttle_presence_publish(user: str) -> bool:
    """
    Prevent flooding presence events.

    Returns True when publish should be skipped.
    """

    cache = frappe.cache()
    key = rate_limit_key("chat", "presence", "broadcast", user)

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
        frappe.log_error("Chat operation failed.", "AOS Touch User Activity Failed")


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

    # Direct push, usually for one peer. Do not leak presence across a block
    # in either direction, even when the old conversation remains in history.
    if to_user:
        if to_user in get_blocked_user_set(user, [to_user]):
            return
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
    """Publish the current persisted presence snapshot to allowed peers.

    This compatibility helper performs no database write. New mutation/read
    paths should call ``schedule_presence_update_to_peers`` so ``last_active``
    is persisted inside the caller-managed transaction and realtime is emitted
    only after that transaction commits.
    """

    publish_presence_update(user=user, to_user=None)


def schedule_presence_update_to_peers(user: str) -> None:
    """Persist activity now and publish it only after commit."""

    if not user:
        return
    touch_user_activity(user)
    from aos.services.chat.events import after_commit

    after_commit(lambda: publish_presence_update(user=user, to_user=None))



def get_presence_impl(**kwargs):
    """Return the other participant's current privacy-safe presence snapshot."""
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "presence", "get", current_user),
        ttl_seconds=60,
        limit=GET_PRESENCE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many presence requests. Please try again shortly.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")
    if not conv_id:
        return fail("conversation_id is required.", error="VALIDATION_ERROR")

    try:
        conv_row = _get_conversation_participants(conv_id)
        if not conv_row:
            return fail("Conversation not found.", error="NOT_FOUND", http_status=404)
        if not _validate_participant(conv_row, current_user):
            return fail("Not allowed.", error="PERMISSION_DENIED", http_status=403)

        peer = _get_other_participant(conv_row, current_user)
        if not peer:
            return fail("Conversation not found.", error="NOT_FOUND", http_status=404)
        if peer in get_blocked_user_set(current_user, [peer]):
            return fail("Not allowed.", error="PERMISSION_DENIED", http_status=403)

        return ok("Presence fetched.", data=_presence_payload(peer))
    except Exception:
        frappe.log_error("Chat operation failed.", "AOS Get Presence Failed")
        return fail("Failed to fetch presence.", error="INTERNAL_ERROR", http_status=500)

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
        key=rate_limit_key("chat", "typing", current_user),
        ttl_seconds=60,
        limit=SEND_TYPING_LIMIT_PER_MINUTE_PER_USER,
        message="Too many typing events. Please slow down.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")
    is_typing = _as_bool(kwargs.get("is_typing"))

    if not conv_id:
        return fail("conversation_id is required.", error="VALIDATION_ERROR")

    try:
        conv_row = _get_conversation_participants(conv_id)
        if not conv_row:
            return fail("Conversation not found.", error="NOT_FOUND")

        if not _validate_participant(conv_row, current_user):
            return fail("Not allowed.", error="PERMISSION_DENIED")

        receiver = _get_other_participant(conv_row, current_user)
        if not receiver:
            return fail("Receiver not found.", error="NOT_FOUND")

        # A historical conversation may remain visible after either participant
        # blocks the other, but transient interaction/presence must not cross
        # that privacy boundary.
        if receiver in get_blocked_user_set(current_user, [receiver]):
            return fail("Not allowed.", error="PERMISSION_DENIED", http_status=403)

        # Typing is also user activity.
        touch_user_activity(current_user)

        sender = _get_user_summary(current_user)

        frappe.publish_realtime(
            event="aos_typing",
            message={
                "conversation_id": conv_id,
                "from": sender["account_id"],
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
        frappe.log_error("Chat operation failed.", "AOS Typing Event Failed")
        return fail("Failed to send typing event.", error="INTERNAL_ERROR")
