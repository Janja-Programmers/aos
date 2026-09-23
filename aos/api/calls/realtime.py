"""
Realtime events for calls.
"""

from __future__ import annotations
from typing import Any

import frappe

from aos.api.shared.user_display import get_user_display, get_user_display_map
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.calls.identifiers import public_call_id
from aos.services.calls.livekit import call_rtc_ready

# HELPERS
def _get_user_summary(
    user_id: str | None,
    user_summaries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return display-safe public identity metadata without email fallback."""
    if not user_id:
        return None
    if user_summaries and user_id in user_summaries:
        return user_summaries[user_id]
    try:
        return get_user_display(user_id)
    except Exception:
        public_id = public_account_id_for_user(user_id)
        return {
            "account_id": public_id,
            "display_name": "AOS User",
            "avatar": None,
            "is_deleted": False,
            "is_live": False,
            "live_id": None,
            "live_status": None,
        }


def serialize_call_for_realtime(
    call,
    *,
    current_user: str | None = None,
    event_status: str | None = None,
    actor: str | None = None,
    user_summaries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the canonical public Calls API/realtime payload."""
    if user_summaries is None:
        users = {
            value
            for value in (
                getattr(call, "caller", None),
                getattr(call, "receiver", None),
                actor,
                getattr(call, "ended_by", None),
                getattr(call, "video_upgrade_requested_by", None),
            )
            if value
        }
        try:
            user_summaries = get_user_display_map(users)
        except Exception:
            user_summaries = {}

    caller = _get_user_summary(call.caller, user_summaries)
    receiver = _get_user_summary(call.receiver, user_summaries)

    other = None
    if current_user:
        if current_user == call.caller:
            other = receiver
        elif current_user == call.receiver:
            other = caller

    actor_summary = _get_user_summary(actor, user_summaries) if actor else None
    upgrade_requester = _get_user_summary(
        getattr(call, "video_upgrade_requested_by", None), user_summaries
    )
    ended_by = _get_user_summary(getattr(call, "ended_by", None), user_summaries)

    call_id = public_call_id(call)

    return {
        "call_id": call_id,
        "conversation_id": call.conversation,
        "status": call.status,
        "event_status": event_status or call.status,
        "state_version": int(getattr(call, "state_version", 1) or 1),
        "rtc_ready": call_rtc_ready(call),
        "ring_expires_at": getattr(call, "ring_expires_at", None),
        "call_type": call.call_type,
        "is_active": bool(call.is_active),
        "caller": caller.get("account_id") if caller else None,
        "caller_display_name": caller.get("display_name") if caller else "AOS User",
        "caller_avatar": caller.get("avatar") if caller else None,
        "caller_is_deleted": bool(caller.get("is_deleted")) if caller else False,
        "caller_is_live": bool(caller.get("is_live")) if caller and not bool(caller.get("is_deleted")) else False,
        "caller_live_id": caller.get("live_id") if caller and not bool(caller.get("is_deleted")) else None,
        "caller_live_status": caller.get("live_status") if caller and not bool(caller.get("is_deleted")) else None,
        "receiver": receiver.get("account_id") if receiver else None,
        "receiver_display_name": receiver.get("display_name") if receiver else "AOS User",
        "receiver_avatar": receiver.get("avatar") if receiver else None,
        "receiver_is_deleted": bool(receiver.get("is_deleted")) if receiver else False,
        "receiver_is_live": bool(receiver.get("is_live")) if receiver and not bool(receiver.get("is_deleted")) else False,
        "receiver_live_id": receiver.get("live_id") if receiver and not bool(receiver.get("is_deleted")) else None,
        "receiver_live_status": receiver.get("live_status") if receiver and not bool(receiver.get("is_deleted")) else None,
        "other_user": other.get("account_id") if other else None,
        "other_display_name": other.get("display_name") if other else None,
        "other_avatar": other.get("avatar") if other else None,
        "other_is_deleted": bool(other.get("is_deleted")) if other else False,
        "other_is_live": bool(other.get("is_live")) if other and not bool(other.get("is_deleted")) else False,
        "other_live_id": other.get("live_id") if other and not bool(other.get("is_deleted")) else None,
        "other_live_status": other.get("live_status") if other and not bool(other.get("is_deleted")) else None,
        "actor": actor_summary.get("account_id") if actor_summary else None,
        "actor_display_name": actor_summary.get("display_name") if actor_summary else None,
        "actor_avatar": actor_summary.get("avatar") if actor_summary else None,
        "actor_is_deleted": bool(actor_summary.get("is_deleted")) if actor_summary else False,
        "actor_is_live": bool(actor_summary.get("is_live")) if actor_summary and not bool(actor_summary.get("is_deleted")) else False,
        "actor_live_id": actor_summary.get("live_id") if actor_summary and not bool(actor_summary.get("is_deleted")) else None,
        "actor_live_status": actor_summary.get("live_status") if actor_summary and not bool(actor_summary.get("is_deleted")) else None,
        "video_upgrade_status": getattr(call, "video_upgrade_status", None) or "none",
        "video_upgrade_requested_by": upgrade_requester.get("account_id") if upgrade_requester else None,
        "video_upgrade_requested_at": getattr(call, "video_upgrade_requested_at", None),
        "video_upgrade_responded_at": getattr(call, "video_upgrade_responded_at", None),
        "ringing_at": call.ringing_at,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "ended_by": ended_by.get("account_id") if ended_by else None,
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
            after_commit=True,
        )


# EVENTS
def publish_call_ready(call):
    """Tell only the caller that fail-closed RTC provisioning completed."""
    message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
    )
    frappe.publish_realtime(
        event="aos_call_ready",
        message=message,
        user=call.caller,
        after_commit=True,
    )


def publish_call_failed_to_caller(call):
    """Close caller UI when provisioning failed before receiver dispatch."""
    message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
        event_status="failed",
    )
    frappe.publish_realtime(
        event="aos_call_ended",
        message=message,
        user=call.caller,
        after_commit=True,
    )


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
        after_commit=True,
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
        after_commit=True,
    )


def publish_call_accepted(call):
    """Publish acceptance to every session of both participants."""
    caller_message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
        event_status="accepted",
        actor=call.receiver,
    )
    receiver_message = serialize_call_for_realtime(
        call,
        current_user=call.receiver,
        event_status="accepted",
        actor=call.receiver,
    )
    frappe.publish_realtime(
        event="aos_call_accepted",
        message=caller_message,
        user=call.caller,
        after_commit=True,
    )
    if call.receiver != call.caller:
        frappe.publish_realtime(
            event="aos_call_accepted",
            message=receiver_message,
            user=call.receiver,
            after_commit=True,
        )


def publish_call_rejected(call):
    """Publish decline to every session of both participants."""
    caller_message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
        event_status="rejected",
        actor=call.receiver,
    )
    receiver_message = serialize_call_for_realtime(
        call,
        current_user=call.receiver,
        event_status="rejected",
        actor=call.receiver,
    )
    frappe.publish_realtime(
        event="aos_call_rejected",
        message=caller_message,
        user=call.caller,
        after_commit=True,
    )
    if call.receiver != call.caller:
        frappe.publish_realtime(
            event="aos_call_rejected",
            message=receiver_message,
            user=call.receiver,
            after_commit=True,
        )


def publish_call_cancelled(call):
    """Publish caller cancellation to every session of both participants."""
    caller_message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
        event_status="cancelled",
        actor=call.caller,
    )
    receiver_message = serialize_call_for_realtime(
        call,
        current_user=call.receiver,
        event_status="cancelled",
        actor=call.caller,
    )
    frappe.publish_realtime(
        event="aos_call_cancelled",
        message=caller_message,
        user=call.caller,
        after_commit=True,
    )
    if call.receiver != call.caller:
        frappe.publish_realtime(
            event="aos_call_cancelled",
            message=receiver_message,
            user=call.receiver,
            after_commit=True,
        )


def publish_call_ended(call, *, event_status: str = "ended"):
    """
    Notify both participants that call ended.

    Sent to:
    - caller
    - receiver
    """

    caller_message = serialize_call_for_realtime(
        call,
        current_user=call.caller,
        event_status=event_status,
        actor=call.ended_by,
    )

    receiver_message = serialize_call_for_realtime(
        call,
        current_user=call.receiver,
        event_status=event_status,
        actor=call.ended_by,
    )

    frappe.publish_realtime(
        event="aos_call_ended",
        message=caller_message,
        user=call.caller,
        after_commit=True,
    )

    if call.receiver != call.caller:
        frappe.publish_realtime(
            event="aos_call_ended",
            message=receiver_message,
            user=call.receiver,
            after_commit=True,
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
        after_commit=True,
    )

    if call.receiver != call.caller:
        frappe.publish_realtime(
            event="aos_call_not_answered",
            message=receiver_message,
            user=call.receiver,
            after_commit=True,
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
        after_commit=True,
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
        after_commit=True,
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
        after_commit=True,
    )
