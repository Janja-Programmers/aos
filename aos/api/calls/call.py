"""
Call APIs (implementation).

Handles:
- initiate_call
- mark_call_ringing
- accept_call
- reject_call
- cancel_call
- end_call
- request_video_upgrade
- respond_video_upgrade
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime, get_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.blocking import ensure_not_blocked
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.user_display import get_user_display

from aos.services.livekit_service import LiveKitService
from aos.services.notification_service import NotificationService

from .constants import (
    INITIATE_CALL_LIMIT_PER_MINUTE_PER_USER,
    MARK_RINGING_LIMIT_PER_MINUTE_PER_USER,
    ACCEPT_CALL_LIMIT_PER_MINUTE_PER_USER,
    REJECT_CALL_LIMIT_PER_MINUTE_PER_USER,
    CANCEL_CALL_LIMIT_PER_MINUTE_PER_USER,
    END_CALL_LIMIT_PER_MINUTE_PER_USER,
    REQUEST_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER,
    RESPOND_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER,
    CALL_TIMEOUT_SECONDS,
    CALL_TIMEOUT_JOB_PATH,
    CALL_TIMEOUT_JOB_QUEUE,
    CALL_TIMEOUT_JOB_EXTRA_BUFFER_SECONDS,
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
    publish_call_ringing,
    publish_call_accepted,
    publish_call_rejected,
    publish_call_cancelled,
    publish_call_ended,
    publish_video_upgrade_requested,
    publish_video_upgrade_accepted,
    publish_video_upgrade_declined,
)


# HELPERS
def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))

    mins = seconds // 60
    secs = seconds % 60

    return f"{mins}m {secs}s" if mins > 0 else f"{secs}s"


def _validate_call_type(call_type: str):
    if call_type not in ("audio", "video"):
        return fail("Invalid call_type.", error="VALIDATION_ERROR")

    return None


def _get_user_display(user: str | None) -> dict:
    """Resolve display-ready user metadata for LiveKit safely."""

    display = get_user_display(user)

    return {
        "display_name": display.get("display_name"),
        "avatar": display.get("avatar"),
    }


def _build_call_metadata(
    *,
    user: str,
    role: str,
    conversation: str,
    call_id: str,
    call_type: str,
) -> str:
    """
    Build LiveKit metadata for call participants.

    Identity remains stable User ID/email. Display name/avatar are metadata
    for clients to render participant UI without guessing from raw emails.
    """
    display = _get_user_display(user)

    return LiveKitService.build_metadata(
        user=user,
        role=role,
        conversation=conversation,
        call_id=call_id,
        call_type=call_type,
        display_name=display.get("display_name"),
        avatar=display.get("avatar"),
        is_guest=False,
    )


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


def _build_incoming_call_push_payload(
    *,
    call,
    receiver: str,
) -> dict:
    """
    Build canonical incoming-call payload for FCM push reconstruction.
    """

    payload = serialize_call_for_realtime(
        call,
        current_user=receiver,
        event_status="incoming",
        actor=call.caller,
    )

    payload.update(
        {
            "event": "aos_incoming_call",
            "type": "incoming_call",
            "notification_type": "incoming_call",
        }
    )

    return payload


def _reload_call(call_id: str):
    return frappe.get_doc("AOS Call", call_id)


def _safe_duration_seconds(started_at, ended_at) -> int:
    if not started_at or not ended_at:
        return 0

    started = get_datetime(started_at)
    ended = get_datetime(ended_at)

    return max(0, int((ended - started).total_seconds()))


def _normalize_video_upgrade_action(action: str | None) -> str:
    action = (action or "").strip().lower()

    if action in ("accept", "accepted"):
        return "accepted"

    if action in ("decline", "declined", "reject", "rejected"):
        return "declined"

    return action


def _enqueue_call_timeout(call_id: str):
    """
    Enqueue a per-call timeout job.

    The queued task sleeps for CALL_TIMEOUT_SECONDS inside the background
    worker, then atomically checks whether the call is still initiated/ringing.
    If yes, it marks the call as missed.

    The API request is not blocked. The cron cleanup remains as fallback.
    """
    if not call_id:
        return

    try:
        frappe.enqueue(
            CALL_TIMEOUT_JOB_PATH,
            queue=CALL_TIMEOUT_JOB_QUEUE,
            timeout=CALL_TIMEOUT_SECONDS + CALL_TIMEOUT_JOB_EXTRA_BUFFER_SECONDS,
            job_id=f"aos_call_timeout:{call_id}",
            enqueue_after_commit=True,
            call_id=call_id,
            delay_seconds=CALL_TIMEOUT_SECONDS,
        )
    except Exception:
        # Do not fail call initiation if timeout scheduling fails.
        # The cron cleanup task remains as a fallback.
        frappe.log_error(
            frappe.get_traceback(),
            f"AOS Call Timeout Enqueue Failed: {call_id}",
        )


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
        return fail("conversation_id is required.", error="VALIDATION_ERROR")

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

        block_err = ensure_not_blocked(
            current_user=current_user,
            target_user=receiver,
            action="call",
        )
        if block_err:
            return block_err

        # Create call.
        call = frappe.new_doc("AOS Call")
        call.conversation = conv_id
        call.caller = current_user
        call.receiver = receiver
        call.call_type = call_type
        call.status = "initiated"
        call.video_upgrade_status = "none"
        call.insert(ignore_permissions=True)

        # Schedule per-call missed timeout.
        # This makes missed-call timing more predictable than relying only on cron.
        _enqueue_call_timeout(call.name)

        # System message.
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=conv_id,
            content="📞 Calling...",
        )

        # Realtime incoming call event.
        publish_incoming_call(call, receiver)

        # Notification.
        incoming_payload = _build_incoming_call_push_payload(
            call=call,
            receiver=receiver,
        )

        NotificationService.notify_incoming_call(
            user=receiver,
            caller=current_user,
            call_id=call.name,
            call_type=call.call_type,
            payload=incoming_payload,
        )

        # Generate caller token.
        token = LiveKitService.generate_call_token(
            user=current_user,
            room_name=call.room_name,
            metadata=_build_call_metadata(
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
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Initiate Call Failed",
        )
        frappe.db.rollback()
        return fail("Failed to initiate call.", error="INTERNAL_ERROR")


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
        return fail("call_id is required.", error="VALIDATION_ERROR")

    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err

        err = validate_is_receiver(call, current_user)
        if err:
            return err

        # Idempotent success:
        # If the receiver app retries after the call is already ringing,
        # return success without rewriting ringing_at or publishing duplicate events.
        if call.status == "ringing":
            return ok(
                "Call is already ringing.",
                data=_build_call_response(
                    call=call,
                    current_user=current_user,
                ),
            )

        err = validate_can_mark_ringing(call)
        if err:
            return err

        now = now_datetime()

        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET
                status = 'ringing',
                ringing_at = COALESCE(ringing_at, %s)
            WHERE name = %s
              AND receiver = %s
              AND status = 'initiated'
            """,
            (now, call_id, current_user),
        )

        if frappe.db._cursor.rowcount == 0:
            return fail("Call cannot be marked as ringing.", error="INVALID_STATE")

        call = _reload_call(call_id)

        # Notify caller that receiver's device/app is now ringing.
        publish_call_ringing(call)

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
        return fail("Failed to mark call as ringing.", error="INTERNAL_ERROR")


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
        return fail("call_id is required.", error="VALIDATION_ERROR")

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
            return fail("Call cannot be accepted.", error="INVALID_STATE")

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
            metadata=_build_call_metadata(
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
        return fail("Failed to accept call.", error="INTERNAL_ERROR")


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
        return fail("call_id is required.", error="VALIDATION_ERROR")

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
            return fail("Call cannot be rejected.", error="INVALID_STATE")

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
        return fail("Failed to reject call.", error="INTERNAL_ERROR")


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
        return fail("call_id is required.", error="VALIDATION_ERROR")

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
            return fail("Call cannot be cancelled.", error="INVALID_STATE")

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
        return fail("Failed to cancel call.", error="INTERNAL_ERROR")


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
        return fail("call_id is required.", error="VALIDATION_ERROR")

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
            return fail("Call cannot be ended.", error="INVALID_STATE")

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
        return fail("Failed to end call.", error="INTERNAL_ERROR")


# REQUEST VIDEO UPGRADE
def request_video_upgrade_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:calls:request-video:user:{current_user}",
        ttl_seconds=60,
        limit=REQUEST_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many video upgrade requests. Please try again shortly.",
    )
    if rl:
        return rl

    call_id = kwargs.get("call_id")

    if not call_id:
        return fail("call_id is required.", error="VALIDATION_ERROR")

    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err

        err = validate_user_in_call(call, current_user)
        if err:
            return err

        if call.status != "ongoing":
            return fail(
                "Video upgrade can only be requested during an ongoing call.",
                error="INVALID_STATE",
            )

        if call.call_type != "audio":
            return fail(
                "Only audio calls can be upgraded to video.",
                error="INVALID_STATE",
            )

        if (call.video_upgrade_status or "none") == "requested":
            return fail(
                "A video upgrade request is already pending.",
                error="INVALID_STATE",
            )

        now = now_datetime()

        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET
                video_upgrade_status = 'requested',
                video_upgrade_requested_by = %s,
                video_upgrade_requested_at = %s,
                video_upgrade_responded_at = NULL
            WHERE name = %s
              AND status = 'ongoing'
              AND call_type = 'audio'
              AND (caller = %s OR receiver = %s)
              AND IFNULL(video_upgrade_status, 'none') != 'requested'
            """,
            (current_user, now, call_id, current_user, current_user),
        )

        if frappe.db._cursor.rowcount == 0:
            return fail(
                "Video upgrade cannot be requested.",
                error="INVALID_STATE",
            )

        call = _reload_call(call_id)

        publish_video_upgrade_requested(call)

        return ok(
            "Video upgrade requested.",
            data=_build_call_response(
                call=call,
                current_user=current_user,
            ),
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Request Video Upgrade Failed",
        )
        frappe.db.rollback()
        return fail("Failed to request video upgrade.", error="INTERNAL_ERROR")


# RESPOND VIDEO UPGRADE
def respond_video_upgrade_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:calls:respond-video:user:{current_user}",
        ttl_seconds=60,
        limit=RESPOND_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many video upgrade responses. Please try again shortly.",
    )
    if rl:
        return rl

    call_id = kwargs.get("call_id")
    action = _normalize_video_upgrade_action(kwargs.get("action"))

    if not call_id:
        return fail("call_id is required.", error="VALIDATION_ERROR")

    if action not in ("accepted", "declined"):
        return fail(
            "action must be accepted or declined.",
            error="VALIDATION_ERROR",
        )

    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err

        err = validate_user_in_call(call, current_user)
        if err:
            return err

        if call.status != "ongoing":
            return fail(
                "Video upgrade can only be answered during an ongoing call.",
                error="INVALID_STATE",
            )

        if call.call_type != "audio":
            return fail(
                "This call is not awaiting an audio-to-video upgrade.",
                error="INVALID_STATE",
            )

        if (call.video_upgrade_status or "none") != "requested":
            return fail(
                "No video upgrade request is pending.",
                error="INVALID_STATE",
            )

        if not call.video_upgrade_requested_by:
            return fail(
                "Invalid video upgrade request.",
                error="INVALID_STATE",
            )

        if call.video_upgrade_requested_by == current_user:
            return fail(
                "You cannot respond to your own video upgrade request.",
                error="PERMISSION_DENIED",
            )

        now = now_datetime()

        if action == "accepted":
            frappe.db.sql(
                """
                UPDATE `tabAOS Call`
                SET
                    call_type = 'video',
                    video_upgrade_status = 'accepted',
                    video_upgrade_responded_at = %s
                WHERE name = %s
                  AND status = 'ongoing'
                  AND call_type = 'audio'
                  AND video_upgrade_status = 'requested'
                  AND video_upgrade_requested_by != %s
                  AND (caller = %s OR receiver = %s)
                """,
                (now, call_id, current_user, current_user, current_user),
            )

            if frappe.db._cursor.rowcount == 0:
                return fail(
                    "Video upgrade cannot be accepted.",
                    error="INVALID_STATE",
                )

            call = _reload_call(call_id)

            publish_video_upgrade_accepted(call)

            return ok(
                "Video upgrade accepted.",
                data=_build_call_response(
                    call=call,
                    current_user=current_user,
                ),
            )

        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET
                video_upgrade_status = 'declined',
                video_upgrade_responded_at = %s
            WHERE name = %s
              AND status = 'ongoing'
              AND call_type = 'audio'
              AND video_upgrade_status = 'requested'
              AND video_upgrade_requested_by != %s
              AND (caller = %s OR receiver = %s)
            """,
            (now, call_id, current_user, current_user, current_user),
        )

        if frappe.db._cursor.rowcount == 0:
            return fail(
                "Video upgrade cannot be declined.",
                error="INVALID_STATE",
            )

        call = _reload_call(call_id)

        publish_video_upgrade_declined(call)

        return ok(
            "Video upgrade declined.",
            data=_build_call_response(
                call=call,
                current_user=current_user,
            ),
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Respond Video Upgrade Failed",
        )
        frappe.db.rollback()
        return fail("Failed to respond to video upgrade.", error="INTERNAL_ERROR")
