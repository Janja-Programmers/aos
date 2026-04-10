"""
Call APIs (implementation).

Handles:
- initiate_call
- accept_call
- reject_call
- end_call
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from aos.services.livekit_service import LiveKitService
from aos.services.notification_service import NotificationService

from .constants import (
    INITIATE_CALL_LIMIT_PER_MINUTE_PER_USER,
    ACCEPT_CALL_LIMIT_PER_MINUTE_PER_USER,
    REJECT_CALL_LIMIT_PER_MINUTE_PER_USER,
    END_CALL_LIMIT_PER_MINUTE_PER_USER,
)

from .validators import (
    validate_conversation_exists,
    validate_user_in_conversation,
    validate_no_active_call_for_conversation,
    validate_call_exists,
    validate_user_in_call,
    validate_is_receiver,
    validate_can_accept,
    validate_can_reject,
    validate_can_end,
)

from .utils import upsert_call_system_message

from .realtime import (
    publish_incoming_call,
    publish_call_accepted,
    publish_call_rejected,
    publish_call_ended,
)


# HELPERS
def _format_duration(seconds: int) -> str:
    mins = seconds // 60
    secs = seconds % 60

    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


# INITIATE CALL
def initiate_call_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:calls:initiate:user:{current_user}",
        ttl_seconds=60,
        limit=INITIATE_CALL_LIMIT_PER_MINUTE_PER_USER,
        message="Too many call attempts. Please try again shortly.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")
    call_type = (kwargs.get("call_type") or "audio").strip()

    if not conv_id:
        return fail("conversation_id is required.", code="VALIDATION_ERROR")

    try:
        conv, err = validate_conversation_exists(conv_id)
        if err:
            return err

        err = validate_user_in_conversation(conv, current_user)
        if err:
            return err

        err = validate_no_active_call_for_conversation(conv_id)
        if err:
            return err

        receiver = (
            conv.participant_2
            if conv.participant_1 == current_user
            else conv.participant_1
        )

        # Create call
        call = frappe.new_doc("AOS Call")
        call.conversation = conv_id
        call.caller = current_user
        call.receiver = receiver
        call.call_type = call_type
        call.status = "initiated"
        call.insert(ignore_permissions=True)

        # System message
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=conv_id,
            content="📞 Calling...",
        )

        # Realtime
        publish_incoming_call(call, receiver)

        # Notification
        NotificationService.notify_incoming_call(
            user=receiver,
            caller=current_user,
            call_id=call.name,
            call_type=call.call_type,
        )

        # Generate token
        token = LiveKitService.generate_call_token(
            user=current_user,
            room_name=call.room_name,
            metadata=LiveKitService.build_metadata(
                user=current_user,
                role="caller",
                conversation=conv_id,
                call_id=call.name,
                call_type=call.call_type,
            ),
        )

        return ok(
            "Call initiated.",
            data={
                "call_id": call.name,
                "room_name": call.room_name,
                "token": token,
                "ws_url": LiveKitService.get_ws_url(),
                "receiver": receiver,
                "call_type": call.call_type,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Initiate Call Failed",
        )
        frappe.db.rollback()
        return fail("Failed to initiate call.", code="INTERNAL_ERROR")


# ACCEPT CALL
def accept_call_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:calls:accept:user:{current_user}",
        ttl_seconds=60,
        limit=ACCEPT_CALL_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    call_id = kwargs.get("call_id")

    if not call_id:
        return fail("call_id is required.", code="VALIDATION_ERROR")

    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err

        err = validate_user_in_call(call, current_user)
        if err:
            return err

        err = validate_is_receiver(call, current_user)
        if err:
            return err

        err = validate_can_accept(call)
        if err:
            return err

        now = now_datetime()

        # Update call
        frappe.db.set_value(
            "AOS Call",
            call_id,
            {
                "status": "ongoing",
                "started_at": now,
            },
            update_modified=False,
        )

        # System message
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content="📞 Call started",
        )

        # Notify caller
        publish_call_accepted(call)

        token = LiveKitService.generate_call_token(
            user=current_user,
            room_name=call.room_name,
            metadata=LiveKitService.build_metadata(
                user=current_user,
                role="receiver",
                conversation=call.conversation,
                call_id=call.name,
                call_type=call.call_type,
            ),
        )

        return ok(
            "Call accepted.",
            data={
                "call_id": call.name,
                "room_name": call.room_name,
                "token": token,
                "ws_url": LiveKitService.get_ws_url(),
                "caller": call.caller,
                "call_type": call.call_type,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Accept Call Failed",
        )
        frappe.db.rollback()
        return fail("Failed to accept call.", code="INTERNAL_ERROR")


# REJECT CALL
def reject_call_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:calls:reject:user:{current_user}",
        ttl_seconds=60,
        limit=REJECT_CALL_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    call_id = kwargs.get("call_id")

    if not call_id:
        return fail("call_id is required.", code="VALIDATION_ERROR")

    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err

        err = validate_user_in_call(call, current_user)
        if err:
            return err

        err = validate_can_reject(call)
        if err:
            return err

        now = now_datetime()

        frappe.db.set_value(
            "AOS Call",
            call_id,
            {
                "status": "rejected",
                "ended_by": current_user,
                "ended_at": now,
                "is_active": 0,
            },
            update_modified=False,
        )

        # System message
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content="📞 Call declined",
        )

        # Notify caller
        publish_call_rejected(call)

        return ok("Call rejected.")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Reject Call Failed",
        )
        frappe.db.rollback()
        return fail("Failed to reject call.", code="INTERNAL_ERROR")


# END CALL
def end_call_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:calls:end:user:{current_user}",
        ttl_seconds=60,
        limit=END_CALL_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    call_id = kwargs.get("call_id")

    if not call_id:
        return fail("call_id is required.", code="VALIDATION_ERROR")

    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err

        err = validate_user_in_call(call, current_user)
        if err:
            return err

        err = validate_can_end(call)
        if err:
            return err

        now = now_datetime()

        duration = 0
        if call.started_at:
            duration = int((now - call.started_at).total_seconds())

        frappe.db.set_value(
            "AOS Call",
            call_id,
            {
                "status": "ended",
                "ended_by": current_user,
                "ended_at": now,
                "duration": duration,
                "is_active": 0,
            },
            update_modified=False,
        )

        # System message
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content=f"📞 Call ended ({_format_duration(duration)})",
        )

        # Notify both users
        publish_call_ended(call)

        return ok("Call ended.")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS End Call Failed",
        )
        frappe.db.rollback()
        return fail("Failed to end call.", code="INTERNAL_ERROR")
