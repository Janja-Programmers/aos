"""
Realtime events for calls.
"""

from __future__ import annotations
from typing import Any

import frappe

# HELPERS
def _get_user_summary(user_id: str | None) -> dict[str, Any] | None:
    """
    Return lightweight user display info.

    Fallback is the user id/email only if full_name is unavailable.
    """

    if not user_id:
        return None

    row = frappe.db.get_value(
        "User",
        user_id,
        ["name", "full_name", "user_image"],
        as_dict=True,
    )

    if not row:
        return {
            "user": user_id,
            "display_name": user_id,
            "avatar": None,
        }

    return {
        "user": row.name,
        "display_name": row.full_name or row.name,
        "avatar": row.user_image,
    }


def serialize_call_for_realtime(
    call,
    *,
    current_user: str | None = None,
    event_status: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """
    Build a consistent realtime/API-friendly call payload.

    current_user is optional. When provided, the payload includes:
    - other_user
    - other_display_name
    - other_avatar

    actor is optional and represents the user who performed the event:
    - incoming
    - ringing
    - accepted
    - rejected
    - cancelled
    - ended
    - video_upgrade_requested
    - video_upgrade_accepted
    - video_upgrade_declined
    - video_upgrade_cancelled
    """

    caller = _get_user_summary(call.caller)
    receiver = _get_user_summary(call.receiver)

    other = None
    if current_user:
        if current_user == call.caller:
            other = receiver
        elif current_user == call.receiver:
            other = caller

    actor_summary = _get_user_summary(actor) if actor else None

    return {
        "id": call.name,
        "call_id": call.name,
        "conversation_id": call.conversation,
        "room_name": call.room_name,
        "status": event_status or call.status,
        "call_type": call.call_type,
        "is_active": call.is_active,
        "caller": caller["user"] if caller else call.caller,
        "caller_display_name": caller["display_name"] if caller else call.caller,
        "caller_avatar": caller["avatar"] if caller else None,
        "receiver": receiver["user"] if receiver else call.receiver,
        "receiver_display_name": (
            receiver["display_name"] if receiver else call.receiver
        ),
        "receiver_avatar": receiver["avatar"] if receiver else None,
        "other_user": other["user"] if other else None,
        "other_display_name": other["display_name"] if other else None,
        "other_avatar": other["avatar"] if other else None,
        "actor": actor_summary["user"] if actor_summary else actor,
        "actor_display_name": (
            actor_summary["display_name"] if actor_summary else actor
        ),
        "actor_avatar": actor_summary["avatar"] if actor_summary else None,
        "video_upgrade_status": getattr(call, "video_upgrade_status", None) or "none",
        "video_upgrade_requested_by": getattr(
            call,
            "video_upgrade_requested_by",
            None,
        ),
        "video_upgrade_requested_at": getattr(
            call,
            "video_upgrade_requested_at",
            None,
        ),
        "video_upgrade_responded_at": getattr(
            call,
            "video_upgrade_responded_at",
            None,
        ),
        "ringing_at": call.ringing_at,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "ended_by": call.ended_by,
        "duration": call.duration or 0,
    }


def _publish(event: str, message: dict, users: list[str]):
    """
    Safe publish helper.
    Avoid duplicate sends if same user appears twice.
    """
    for user in set(users):
        if not user:
            continue

        frappe.publish_realtime(
            event=event,
            message=message,
            user=user,
        )


# EVENTS
def publish_incoming_call(call, receiver: str):
    """
    Notify receiver that caller is calling.

    Sent to:
    - receiver only
    """

    message = serialize_call_for_realtime(
        call,
        current_user=receiver,
        event_status="incoming",
        actor=call.caller,
    )

    frappe.publish_realtime(
        event="aos_incoming_call",
        message=message,
        user=receiver,
    )


def publish_call_ringing(call):
    """
    Notify caller that receiver's device/app is ringing.

    Sent to:
    - caller only
    """

    message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
        event_status="ringing",
        actor=call.receiver,
    )

    frappe.publish_realtime(
        event="aos_call_ringing",
        message=message,
        user=call.caller,
    )


def publish_call_accepted(call):
    """
    Notify caller that receiver accepted.

    Sent to:
    - caller only
    """

    message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
        event_status="accepted",
        actor=call.receiver,
    )

    frappe.publish_realtime(
        event="aos_call_accepted",
        message=message,
        user=call.caller,
    )


def publish_call_rejected(call):
    """
    Notify caller that receiver rejected.

    Sent to:
    - caller only
    """

    message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
        event_status="rejected",
        actor=call.receiver,
    )

    frappe.publish_realtime(
        event="aos_call_rejected",
        message=message,
        user=call.caller,
    )


def publish_call_cancelled(call):
    """
    Notify receiver that caller cancelled before answer.

    Sent to:
    - receiver only
    """

    message = serialize_call_for_realtime(
        call,
        current_user=call.receiver,
        event_status="cancelled",
        actor=call.caller,
    )

    frappe.publish_realtime(
        event="aos_call_cancelled",
        message=message,
        user=call.receiver,
    )


def publish_call_ended(call):
    """
    Notify both participants that call ended.

    Sent to:
    - caller
    - receiver
    """

    caller_message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
        event_status="ended",
        actor=call.ended_by,
    )

    receiver_message = serialize_call_for_realtime(
        call,
        current_user=call.receiver,
        event_status="ended",
        actor=call.ended_by,
    )

    frappe.publish_realtime(
        event="aos_call_ended",
        message=caller_message,
        user=call.caller,
    )

    if call.receiver != call.caller:
        frappe.publish_realtime(
            event="aos_call_ended",
            message=receiver_message,
            user=call.receiver,
        )


def publish_call_not_answered(call):
    """
    Notify both participants that call was not answered/missed.

    Sent to:
    - caller
    - receiver
    """

    caller_message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
        event_status="not_answered",
        actor=None,
    )

    receiver_message = serialize_call_for_realtime(
        call,
        current_user=call.receiver,
        event_status="missed",
        actor=None,
    )

    frappe.publish_realtime(
        event="aos_call_not_answered",
        message=caller_message,
        user=call.caller,
    )

    if call.receiver != call.caller:
        frappe.publish_realtime(
            event="aos_call_not_answered",
            message=receiver_message,
            user=call.receiver,
        )


# VIDEO UPGRADE EVENTS
def publish_video_upgrade_requested(call):
    """
    Notify the other participant that video upgrade was requested.

    Sent to:
    - non-requesting participant only
    """

    requester = call.video_upgrade_requested_by

    if not requester:
        return

    target = call.receiver if requester == call.caller else call.caller

    message = serialize_call_for_realtime(
        call,
        current_user=target,
        event_status="video_upgrade_requested",
        actor=requester,
    )

    frappe.publish_realtime(
        event="aos_call_video_upgrade_requested",
        message=message,
        user=target,
    )


def publish_video_upgrade_accepted(call):
    """
    Notify both participants that video upgrade was accepted.

    Sent to:
    - caller
    - receiver
    """

    actor = (
        call.receiver
        if call.video_upgrade_requested_by == call.caller
        else call.caller
    )

    caller_message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
        event_status="video_upgrade_accepted",
        actor=actor,
    )

    receiver_message = serialize_call_for_realtime(
        call,
        current_user=call.receiver,
        event_status="video_upgrade_accepted",
        actor=actor,
    )

    _publish(
        "aos_call_video_upgrade_accepted",
        caller_message,
        [call.caller],
    )

    if call.receiver != call.caller:
        _publish(
            "aos_call_video_upgrade_accepted",
            receiver_message,
            [call.receiver],
        )


def publish_video_upgrade_declined(call):
    """
    Notify requester that video upgrade was declined.

    Sent to:
    - requester only
    """

    requester = call.video_upgrade_requested_by

    if not requester:
        return

    actor = call.receiver if requester == call.caller else call.caller

    message = serialize_call_for_realtime(
        call,
        current_user=requester,
        event_status="video_upgrade_declined",
        actor=actor,
    )

    frappe.publish_realtime(
        event="aos_call_video_upgrade_declined",
        message=message,
        user=requester,
    )


def publish_video_upgrade_cancelled(call):
    """
    Notify the other participant that pending video upgrade was cancelled.

    Sent to:
    - non-cancelling participant only
    """

    requester = call.video_upgrade_requested_by

    if not requester:
        return

    target = call.receiver if requester == call.caller else call.caller

    message = serialize_call_for_realtime(
        call,
        current_user=target,
        event_status="video_upgrade_cancelled",
        actor=requester,
    )

    frappe.publish_realtime(
        event="aos_call_video_upgrade_cancelled",
        message=message,
        user=target,
    )
