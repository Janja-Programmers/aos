"""
Call APIs (implementation).

Handles:
- initiate_call
- mark_call_ringing
- accept_call
- reject_call
- cancel_call
- end_call
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime, get_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from aos.services.livekit_service import LiveKitService
from aos.services.notification_service import NotificationService

from .constants import (
    INITIATE_CALL_LIMIT_PER_MINUTE_PER_USER,
    MARK_RINGING_LIMIT_PER_MINUTE_PER_USER,
    ACCEPT_CALL_LIMIT_PER_MINUTE_PER_USER,
    REJECT_CALL_LIMIT_PER_MINUTE_PER_USER,
    CANCEL_CALL_LIMIT_PER_MINUTE_PER_USER,
    END_CALL_LIMIT_PER_MINUTE_PER_USER,
)

from .validators import (
    validate_conversation_exists,
    validate_user_in_conversation,
    validate_no_active_call_for_conversation,
    validate_call_exists,
    validate_user_in_call,
    validate_is_receiver,
    validate_is_caller,
    validate_can_accept,
    validate_can_reject,
    validate_can_end,
    validate_can_cancel,
    validate_can_mark_ringing,
)

from .utils import upsert_call_system_message

from .realtime import (
    serialize_call_for_realtime,
    publish_incoming_call,
    publish_call_accepted,
    publish_call_rejected,
    publish_call_cancelled,
    publish_call_ended,
)


# HELPERS
def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))

    mins = seconds // 60
    secs = seconds % 60

    return f"{mins}m {secs}s" if mins > 0 else f"{secs}s"


def _validate_call_type(call_type: str):
    if call_type not in ("audio", "video"):
        return fail("Invalid call_type.", code="VALIDATION_ERROR")

    return None


def _build_call_response(
    *,
    call,
    current_user: str,
    token: str | None = None,
) -> dict:
    """
    Build API response payload.

    AOS Call stores stable User IDs/emails in caller/receiver.
    This response adds display-ready user fields through the shared serializer.
    """

    data = serialize_call_for_realtime(
        call,
        current_user=current_user,
    )

    if token:
        data["token"] = token
        data["ws_url"] = LiveKitService.get_ws_url()

    return data


def _reload_call(call_id: str):
    return frappe.get_doc("AOS Call", call_id)


def _safe_duration_seconds(started_at, ended_at) -> int:
    if not started_at or not ended_at:
        return 0

    started = get_datetime(started_at)
    ended = get_datetime(ended_at)

    return max(0, int((ended - started).total_seconds()))


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
    call_type = (kwargs.get("call_type") or "audio").strip().lower()

    if not conv_id:
        return fail("conversation_id is required.", code="VALIDATION_ERROR")

    call_type_error = _validate_call_type(call_type)
    if call_type_error:
        return call_type_error

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

        # Create call.
        call = frappe.new_doc("AOS Call")
        call.conversation = conv_id
        call.caller = current_user
        call.receiver = receiver
        call.call_type = call_type
        call.status = "initiated"
        call.insert(ignore_permissions=True)

        # System message.
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=conv_id,
            content="📞 Calling...",
        )

        # Realtime incoming call event.
        publish_incoming_call(call, receiver)

        # Notification.
        NotificationService.notify_incoming_call(
            user=receiver,
            caller=current_user,
            call_id=call.name,
            call_type=call.call_type,
        )

        # Generate caller token.
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
            data=_build_call_response(
                call=call,
                current_user=current_user,
                token=token,
            ),
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Initiate Call Failed",
        )
        frappe.db.rollback()
        return fail("Failed to initiate call.", code="INTERNAL_ERROR")


# MARK CALL RINGING
def mark_call_ringing_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:calls:ringing:user:{current_user}",
        ttl_seconds=60,
        limit=MARK_RINGING_LIMIT_PER_MINUTE_PER_USER,
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

        err = validate_is_receiver(call, current_user)
        if err:
            return err

        err = validate_can_mark_ringing(call)
        if err:
            return err

        now = now_datetime()

        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET
                status = 'ringing',
                ringing_at = %s
            WHERE name = %s
              AND receiver = %s
              AND status = 'initiated'
            """,
            (now, call_id, current_user),
        )

        if frappe.db._cursor.rowcount == 0:
            return fail("Call cannot be marked as ringing.", code="INVALID_STATE")

        call = _reload_call(call_id)

        return ok(
            "Call marked as ringing.",
            data=_build_call_response(
                call=call,
                current_user=current_user,
            ),
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Mark Ringing Failed",
        )
        frappe.db.rollback()
        return fail("Failed to mark call as ringing.", code="INTERNAL_ERROR")


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

        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET
                status = 'ongoing',
                started_at = %s,
                ringing_at = COALESCE(ringing_at, %s)
            WHERE name = %s
              AND receiver = %s
              AND status IN ('initiated', 'ringing')
            """,
            (now, now, call_id, current_user),
        )

        if frappe.db._cursor.rowcount == 0:
            return fail("Call cannot be accepted.", code="INVALID_STATE")

        call = _reload_call(call_id)

        # System message.
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content="📞 Call started",
        )

        # Notify caller with fresh call state.
        publish_call_accepted(call)

        # Generate receiver token.
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
            data=_build_call_response(
                call=call,
                current_user=current_user,
                token=token,
            ),
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

        err = validate_is_receiver(call, current_user)
        if err:
            return err

        err = validate_can_reject(call)
        if err:
            return err

        now = now_datetime()

        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET
                status = 'rejected',
                ended_by = %s,
                ended_at = %s,
                is_active = 0
            WHERE name = %s
              AND receiver = %s
              AND status IN ('initiated', 'ringing')
            """,
            (current_user, now, call_id, current_user),
        )

        if frappe.db._cursor.rowcount == 0:
            return fail("Call cannot be rejected.", code="INVALID_STATE")

        call = _reload_call(call_id)

        # System message.
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content="📞 Call declined",
        )

        # Notify caller.
        publish_call_rejected(call)

        return ok(
            "Call rejected.",
            data=_build_call_response(
                call=call,
                current_user=current_user,
            ),
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Reject Call Failed",
        )
        frappe.db.rollback()
        return fail("Failed to reject call.", code="INTERNAL_ERROR")


# CANCEL CALL
def cancel_call_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:calls:cancel:user:{current_user}",
        ttl_seconds=60,
        limit=CANCEL_CALL_LIMIT_PER_MINUTE_PER_USER,
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

        err = validate_is_caller(call, current_user)
        if err:
            return err

        err = validate_can_cancel(call)
        if err:
            return err

        now = now_datetime()

        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET
                status = 'cancelled',
                ended_by = %s,
                ended_at = %s,
                is_active = 0
            WHERE name = %s
              AND caller = %s
              AND status IN ('initiated', 'ringing')
            """,
            (current_user, now, call_id, current_user),
        )

        if frappe.db._cursor.rowcount == 0:
            return fail("Call cannot be cancelled.", code="INVALID_STATE")

        call = _reload_call(call_id)

        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content="📞 Call cancelled",
        )

        publish_call_cancelled(call)

        return ok(
            "Call cancelled.",
            data=_build_call_response(
                call=call,
                current_user=current_user,
            ),
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Cancel Call Failed",
        )
        frappe.db.rollback()
        return fail("Failed to cancel call.", code="INTERNAL_ERROR")


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

        duration = _safe_duration_seconds(
            call.started_at,
            now,
        )

        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET
                status = 'ended',
                ended_by = %s,
                ended_at = %s,
                duration = %s,
                is_active = 0
            WHERE name = %s
              AND status = 'ongoing'
            """,
            (current_user, now, duration, call_id),
        )

        if frappe.db._cursor.rowcount == 0:
            return fail("Call cannot be ended.", code="INVALID_STATE")

        call = _reload_call(call_id)

        # System message.
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content=f"📞 Call ended ({_format_duration(duration)})",
        )

        # Notify both users.
        publish_call_ended(call)

        return ok(
            "Call ended.",
            data=_build_call_response(
                call=call,
                current_user=current_user,
            ),
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS End Call Failed",
        )
        frappe.db.rollback()
        return fail("Failed to end call.", code="INTERNAL_ERROR")
