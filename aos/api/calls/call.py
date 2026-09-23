"""Canonical direct + group Calls API implementation."""
from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.api.shared.user_display import get_user_display
from aos.services.calls.errors import CallError
from aos.services.calls.identifiers import public_call_id
from aos.services.calls.livekit import (
    call_rtc_ready,
    enqueue_room_cleanup,
    enqueue_room_provisioning,
    ensure_call_join_ready,
    get_call_ws_url,
    issue_call_token,
    participant_identity,
)
from aos.services.calls.participants import (
    all_invitees_terminal,
    any_invitee_joined,
    create_participant,
    joined_users,
    lock_participant_rows,
    non_initiator_rows,
    participant_for_user,
    participant_user_set,
    set_participant_count,
    users_for_call,
)
from aos.services.calls.policy import (
    active_call_names_for_users,
    ensure_call_interaction_allowed,
    ensure_interaction_allowed,
    lock_call_row,
    lock_users_for_call,
)
from aos.services.calls.reconciliation import clear_missing_room_marker
from aos.services.livekit_service import LiveKitService
from aos.services.notifications.service import NotificationService

from .constants import (
    ACCEPT_CALL_LIMIT_PER_MINUTE_PER_USER,
    ADD_CALL_PARTICIPANTS_LIMIT_PER_MINUTE_PER_USER,
    CALL_RING_TIMEOUT_SECONDS,
    CALL_INVITEE_FANOUT_LIMIT_PER_MINUTE_PER_USER,
    CANCEL_CALL_LIMIT_PER_MINUTE_PER_USER,
    END_CALL_LIMIT_PER_MINUTE_PER_USER,
    INCOMING_CALL_LIMIT_PER_MINUTE_PER_TARGET,
    INITIATE_CALL_LIMIT_PER_MINUTE_PER_USER,
    MARK_RINGING_LIMIT_PER_MINUTE_PER_USER,
    REJECT_CALL_LIMIT_PER_MINUTE_PER_USER,
    REQUEST_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER,
    RESPOND_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER,
)
from .realtime import (
    publish_call_cancelled,
    publish_call_ended,
    publish_incoming_call,
    publish_participant_declined,
    publish_participant_joined,
    publish_participant_left,
    publish_participants_invited,
    publish_participant_ringing,
    publish_video_upgrade_accepted,
    publish_video_upgrade_declined,
    publish_video_upgrade_requested,
    serialize_call_for_realtime,
)
from .utils import upsert_call_system_message
from .validators import (
    parse_participant_ids,
    resolve_participant_users,
    validate_call_exists,
    validate_direct_conversation,
    validate_is_initiator,
    validate_user_in_call,
)


def _validate_call_type(call_type: str):
    return None if call_type in {"audio", "video"} else fail("Invalid call_type.", error="VALIDATION_ERROR")


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    mins, secs = divmod(seconds, 60)
    return f"{mins}m {secs}s" if mins else f"{secs}s"


def _safe_duration_seconds(started_at, ended_at) -> int:
    if not started_at or not ended_at:
        return 0
    return max(0, int((get_datetime(ended_at) - get_datetime(started_at)).total_seconds()))


def _build_metadata(*, user: str, role: str, call) -> str:
    display = get_user_display(user)
    return LiveKitService.build_metadata(
        user=participant_identity(user),
        role=role,
        conversation=call.conversation or "",
        call_id=public_call_id(call),
        call_type=call.call_type,
        display_name=display.get("display_name"),
        avatar=display.get("avatar"),
        is_guest=False,
    )


def _token_for(call, user: str) -> str:
    row = participant_for_user(call.name, user)
    role = "initiator" if row and row.role == "initiator" else "participant"
    return issue_call_token(
        identity=participant_identity(user),
        room_name=call.room_name,
        call_type=call.call_type,
        metadata=_build_metadata(user=user, role=role, call=call),
    )


def _response(call, current_user: str, *, token: str | None = None) -> dict:
    data = serialize_call_for_realtime(call, current_user=current_user)
    if token:
        data.update({"token": token, "ws_url": get_call_ws_url()})
    return data


def _maybe_system_message(call, content: str) -> None:
    if call.conversation:
        upsert_call_system_message(call_id=call.name, conversation_id=call.conversation, content=content)


def _reload(call_name: str):
    return frappe.get_doc("AOS Call", call_name)


def _incoming_payload(call, user: str, *, actor: str) -> dict:
    payload = serialize_call_for_realtime(call, current_user=user, event_status="incoming", actor=actor)
    payload.update({"event": "aos_incoming_call", "type": "incoming_call", "notification_type": "incoming_call"})
    return payload


def _dispatch_invite(call, *, target: str, actor: str, now=None) -> None:
    now = now or now_datetime()
    expires = add_to_date(now, seconds=CALL_RING_TIMEOUT_SECONDS)
    frappe.db.sql(
        """
        UPDATE `tabAOS Call Participant`
        SET incoming_dispatched_at=%s, ring_expires_at=%s
        WHERE `call`=%s AND user=%s AND status='invited' AND incoming_dispatched_at IS NULL
        """,
        (now, expires, call.name, target),
    )
    if frappe.db._cursor.rowcount == 0:
        return
    publish_incoming_call(call, target, actor=actor)
    NotificationService.notify_incoming_call(
        user=target,
        caller=actor,
        call_id=public_call_id(call),
        call_type=call.call_type,
        payload=_incoming_payload(call, target, actor=actor),
    )


def _finalize_unanswered_if_done(call_name: str, *, ended_by: str | None = None):
    if any_invitee_joined(call_name) or not all_invitees_terminal(call_name):
        return _reload(call_name)
    rows = non_initiator_rows(call_name)
    global_status = "rejected" if rows and all(row.status == "declined" for row in rows) else "missed"
    now = now_datetime()
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET status=%s, is_active=0, ended_at=%s, ended_by=%s,
            room_cleanup_pending=1, state_version=state_version+1
        WHERE name=%s AND status IN ('initiated','ringing') AND is_active=1
        """,
        (global_status, now, ended_by, call_name),
    )
    if frappe.db._cursor.rowcount:
        frappe.db.sql(
            "UPDATE `tabAOS Call Participant` SET status='left',left_at=COALESCE(left_at,%s) "
            "WHERE `call`=%s AND role='initiator' AND status='joined'",
            (now, call_name),
        )
    call = _reload(call_name)
    if not int(call.is_active or 0):
        enqueue_room_cleanup(call.name)
    return call


def initiate_call_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "initiate", "user", current_user), ttl_seconds=60, limit=INITIATE_CALL_LIMIT_PER_MINUTE_PER_USER, message="Too many call attempts. Please try again shortly.")):
        return rl

    account_ids, err = parse_participant_ids(kwargs.get("participant_ids"))
    if err:
        return err
    targets, err = resolve_participant_users(account_ids, current_user=current_user)
    if err:
        return err
    call_type = str(kwargs.get("call_type") or "audio").strip().lower()
    if (type_error := _validate_call_type(call_type)):
        return type_error
    conversation_id = str(kwargs.get("conversation_id") or "").strip() or None
    call_mode = "direct" if len(targets) == 1 else "group"
    if call_mode == "group" and conversation_id:
        return fail("Group calls are not bound to one-to-one conversations.", error="VALIDATION_ERROR")
    if call_mode == "direct":
        if (conv_error := validate_direct_conversation(conversation_id, current_user=current_user, target_user=targets[0])):
            return conv_error

    try:
        for target in targets:
            if (fanout_rl := rate_limit(
                key=rate_limit_key("calls", "invite_fanout", "user", current_user),
                ttl_seconds=60,
                limit=CALL_INVITEE_FANOUT_LIMIT_PER_MINUTE_PER_USER,
                message="Too many call invitations. Please try again shortly.",
            )):
                return fanout_rl
            if (interaction_error := ensure_interaction_allowed(current_user=current_user, peer_user=target, action="call")):
                return interaction_error
            if (target_rl := rate_limit(key=rate_limit_key("calls", "initiate", "target", target), ttl_seconds=60, limit=INCOMING_CALL_LIMIT_PER_MINUTE_PER_TARGET, message="One or more accounts are receiving too many call attempts. Please try again shortly.")):
                return target_rl

        all_users = [current_user, *targets]
        lock_users_for_call(*all_users)
        for target in targets:
            if (interaction_error := ensure_interaction_allowed(current_user=current_user, peer_user=target, action="call")):
                return interaction_error

        conflicts = active_call_names_for_users(*all_users)
        if conflicts:
            if len(conflicts) == 1:
                existing = _reload(conflicts[0])
                if (
                    existing.initiator == current_user
                    and existing.call_type == call_type
                    and existing.call_mode == call_mode
                    and participant_user_set(existing.name) == set(all_users)
                ):
                    token = _token_for(existing, current_user) if call_rtc_ready(existing) else None
                    return ok("Call already active.", data=_response(existing, current_user, token=token))
            return fail("A participant is already in another call.", error="ACTIVE_CALL_EXISTS", http_status=409)

        call = frappe.new_doc("AOS Call")
        call.conversation = conversation_id
        call.initiator = current_user
        call.call_mode = call_mode
        call.max_participants = 2 if call_mode == "direct" else 32
        call.participant_count = len(all_users)
        call.call_type = call_type
        call.status = "initiated"
        call.video_upgrade_status = "none"
        call.insert(ignore_permissions=True)

        created_at = now_datetime()
        create_participant(call_name=call.name, user=current_user, role="initiator", status="joined", added_by=current_user, invited_at=created_at)
        for target in targets:
            create_participant(call_name=call.name, user=target, role="participant", status="invited", added_by=current_user, invited_at=created_at)
        enqueue_room_provisioning(call.name)
        return ok("Call initiated.", data=_response(_reload(call.name), current_user))
    except CallError:
        raise
    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Initiate Call Failed")
        return fail("Failed to initiate call.", error="INTERNAL_ERROR")


def mark_call_ringing_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "ringing", current_user), ttl_seconds=60, limit=MARK_RINGING_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")):
        return rl
    call_id = kwargs.get("call_id")
    if not call_id:
        return fail("call_id is required.", error="VALIDATION_ERROR")
    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err
        if (membership := validate_user_in_call(call, current_user)):
            return membership
        lock_call_row(call.name)
        call = _reload(call.name)
        row = participant_for_user(call.name, current_user, for_update=True)
        if not row or row.role == "initiator":
            return fail("Call not found.", error="NOT_FOUND", http_status=404)
        if row.status == "ringing":
            return ok("Call is already ringing.", data=_response(call, current_user))
        if row.status != "invited" or not row.ring_expires_at or get_datetime(row.ring_expires_at) <= get_datetime(now_datetime()):
            return fail("Call is no longer ringing.", error="INVALID_STATE", http_status=409)
        ensure_call_join_ready(call)
        if (policy_error := ensure_call_interaction_allowed(call, current_user, action="receive calls from")):
            return policy_error
        now = now_datetime()
        frappe.db.sql("UPDATE `tabAOS Call Participant` SET status='ringing', ringing_at=COALESCE(ringing_at,%s) WHERE name=%s AND status='invited'", (now, row.name))
        frappe.db.sql("UPDATE `tabAOS Call` SET status=IF(status='initiated','ringing',status), ringing_at=COALESCE(ringing_at,%s), state_version=state_version+1 WHERE name=%s AND status IN ('initiated','ringing','ongoing')", (now, call.name))
        call = _reload(call.name)
        publish_participant_ringing(call, current_user)
        return ok("Call marked as ringing.", data=_response(call, current_user))
    except CallError:
        raise
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Mark Ringing Failed")
        return fail("Failed to mark call as ringing.", error="INTERNAL_ERROR")


def accept_call_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "accept", current_user), ttl_seconds=60, limit=ACCEPT_CALL_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")):
        return rl
    call_id = kwargs.get("call_id")
    if not call_id:
        return fail("call_id is required.", error="VALIDATION_ERROR")
    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err
        if (membership := validate_user_in_call(call, current_user)):
            return membership
        lock_call_row(call.name)
        call = _reload(call.name)
        row = participant_for_user(call.name, current_user, for_update=True)
        if not row or row.role == "initiator":
            return fail("Only invited participants can accept.", error="PERMISSION_DENIED")
        if row.status == "joined" and call.status == "ongoing":
            ensure_call_join_ready(call)
            clear_missing_room_marker(call.name)
            return ok("Call is already accepted.", data=_response(call, current_user, token=_token_for(call, current_user)))
        if call.status not in {"initiated", "ringing", "ongoing"} or row.status not in {"invited", "ringing"}:
            return fail("Call cannot be accepted.", error="INVALID_STATE")
        if not row.ring_expires_at or get_datetime(row.ring_expires_at) <= get_datetime(now_datetime()):
            return fail("Call is no longer ringing.", error="INVALID_STATE", http_status=409)
        ensure_call_join_ready(call)
        if (policy_error := ensure_call_interaction_allowed(call, current_user, action="accept a call from")):
            return policy_error
        now = now_datetime()
        frappe.db.sql("UPDATE `tabAOS Call Participant` SET status='joined', joined_at=COALESCE(joined_at,%s), responded_at=COALESCE(responded_at,%s) WHERE name=%s AND status IN ('invited','ringing') AND ring_expires_at>%s", (now, now, row.name, now))
        if frappe.db._cursor.rowcount == 0:
            return fail("Call cannot be accepted.", error="INVALID_STATE")
        frappe.db.sql("UPDATE `tabAOS Call` SET status='ongoing', started_at=COALESCE(started_at,%s), is_active=1, room_cleanup_pending=0, rtc_missing_since=NULL, state_version=state_version+1 WHERE name=%s AND status IN ('initiated','ringing','ongoing')", (now, call.name))
        call = _reload(call.name)
        _maybe_system_message(call, "📞 Call started")
        publish_participant_joined(call, current_user)
        return ok("Call accepted.", data=_response(call, current_user, token=_token_for(call, current_user)))
    except CallError:
        raise
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Accept Call Failed")
        return fail("Failed to accept call.", error="INTERNAL_ERROR")


def reject_call_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "reject", current_user), ttl_seconds=60, limit=REJECT_CALL_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")):
        return rl
    call_id = kwargs.get("call_id")
    if not call_id:
        return fail("call_id is required.", error="VALIDATION_ERROR")
    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err
        if (membership := validate_user_in_call(call, current_user)):
            return membership
        lock_call_row(call.name)
        call = _reload(call.name)
        row = participant_for_user(call.name, current_user, for_update=True)
        if not row or row.role == "initiator":
            return fail("Only invited participants can reject.", error="PERMISSION_DENIED")
        if row.status == "declined":
            return ok("Call is already rejected.", data=_response(call, current_user))
        if row.status not in {"invited", "ringing"} or not row.ring_expires_at or get_datetime(row.ring_expires_at) <= get_datetime(now_datetime()):
            return fail("Call cannot be rejected.", error="INVALID_STATE")
        now = now_datetime()
        frappe.db.sql("UPDATE `tabAOS Call Participant` SET status='declined', responded_at=%s WHERE name=%s AND status IN ('invited','ringing')", (now, row.name))
        frappe.db.sql("UPDATE `tabAOS Call` SET state_version=state_version+1 WHERE name=%s", (call.name,))
        call = _finalize_unanswered_if_done(call.name, ended_by=current_user)
        publish_participant_declined(call, current_user)
        if not int(call.is_active or 0):
            # Finalizing an unanswered direct call moves the initiator participant
            # to ``left`` before the participant-scoped decline event is built.
            # Publish the terminal Call event as well so every member, including
            # the caller, immediately converges on the durable rejected state.
            publish_call_ended(call, event_status=call.status)
            _maybe_system_message(call, "📞 Call rejected")
        return ok("Call rejected.", data=_response(call, current_user))
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Reject Call Failed")
        return fail("Failed to reject call.", error="INTERNAL_ERROR")


def cancel_call_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "cancel", current_user), ttl_seconds=60, limit=CANCEL_CALL_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")):
        return rl
    call_id = kwargs.get("call_id")
    if not call_id:
        return fail("call_id is required.", error="VALIDATION_ERROR")
    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err
        if (role_error := validate_is_initiator(call, current_user)):
            return role_error
        lock_call_row(call.name)
        call = _reload(call.name)
        if call.status == "cancelled":
            return ok("Call is already cancelled.", data=_response(call, current_user))
        if call.status not in {"initiated", "ringing"} or any_invitee_joined(call.name):
            return fail("Call cannot be cancelled.", error="INVALID_STATE")
        now = now_datetime()
        frappe.db.sql("UPDATE `tabAOS Call Participant` SET status=CASE WHEN role='initiator' THEN 'left' ELSE 'cancelled' END, left_at=CASE WHEN role='initiator' THEN %s ELSE left_at END, responded_at=CASE WHEN role='participant' THEN %s ELSE responded_at END WHERE `call`=%s AND status IN ('invited','ringing','joined')", (now, now, call.name))
        frappe.db.sql("UPDATE `tabAOS Call` SET status='cancelled', ended_by=%s, ended_at=%s, is_active=0, room_cleanup_pending=1, state_version=state_version+1 WHERE name=%s AND status IN ('initiated','ringing')", (current_user, now, call.name))
        call = _reload(call.name)
        enqueue_room_cleanup(call.name)
        _maybe_system_message(call, "📞 Call cancelled")
        publish_call_cancelled(call)
        return ok("Call cancelled.", data=_response(call, current_user))
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Cancel Call Failed")
        return fail("Failed to cancel call.", error="INTERNAL_ERROR")


def end_call_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "end", current_user), ttl_seconds=60, limit=END_CALL_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")):
        return rl
    call_id = kwargs.get("call_id")
    if not call_id:
        return fail("call_id is required.", error="VALIDATION_ERROR")
    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err
        if (membership := validate_user_in_call(call, current_user)):
            return membership
        lock_call_row(call.name)
        call = _reload(call.name)
        row = participant_for_user(call.name, current_user, for_update=True)
        if row and row.status == "left" and call.call_mode == "group":
            return ok("You already left the call.", data=_response(call, current_user))
        if call.status == "ended":
            return ok("Call is already ended.", data=_response(call, current_user))
        if call.status != "ongoing" or not row or row.status != "joined":
            return fail("Call cannot be ended.", error="INVALID_STATE")
        now = now_datetime()
        if call.call_mode == "direct":
            duration = _safe_duration_seconds(call.started_at, now)
            frappe.db.sql("UPDATE `tabAOS Call Participant` SET status='left', left_at=COALESCE(left_at,%s) WHERE `call`=%s AND status='joined'", (now, call.name))
            frappe.db.sql("UPDATE `tabAOS Call` SET status='ended', ended_by=%s, ended_at=%s, duration=%s, is_active=0, room_cleanup_pending=1, rtc_missing_since=NULL, state_version=state_version+1 WHERE name=%s AND status='ongoing'", (current_user, now, duration, call.name))
            call = _reload(call.name)
            enqueue_room_cleanup(call.name)
            _maybe_system_message(call, f"📞 Call ended ({_format_duration(duration)})")
            publish_call_ended(call)
            return ok("Call ended.", data=_response(call, current_user))

        frappe.db.sql("UPDATE `tabAOS Call Participant` SET status='left', left_at=COALESCE(left_at,%s) WHERE name=%s AND status='joined'", (now, row.name))
        frappe.db.sql("UPDATE `tabAOS Call` SET state_version=state_version+1 WHERE name=%s AND status='ongoing'", (call.name,))
        remaining = joined_users(call.name)
        if remaining:
            call = _reload(call.name)
            publish_participant_left(call, current_user)
            return ok("You left the call.", data=_response(call, current_user))
        duration = _safe_duration_seconds(call.started_at, now)
        frappe.db.sql(
            "UPDATE `tabAOS Call Participant` SET status='cancelled',responded_at=COALESCE(responded_at,%s) "
            "WHERE `call`=%s AND status IN ('invited','ringing')",
            (now, call.name),
        )
        frappe.db.sql("UPDATE `tabAOS Call` SET status='ended', ended_by=%s, ended_at=%s, duration=%s, is_active=0, room_cleanup_pending=1, rtc_missing_since=NULL, state_version=state_version+1 WHERE name=%s AND status='ongoing'", (current_user, now, duration, call.name))
        call = _reload(call.name)
        enqueue_room_cleanup(call.name)
        publish_call_ended(call)
        return ok("Call ended.", data=_response(call, current_user))
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS End Call Failed")
        return fail("Failed to end call.", error="INTERNAL_ERROR")


def add_call_participants_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "add_participants", current_user), ttl_seconds=60, limit=ADD_CALL_PARTICIPANTS_LIMIT_PER_MINUTE_PER_USER, message="Too many participant invitations. Please try again shortly.")):
        return rl
    call_id = kwargs.get("call_id")
    account_ids, err = parse_participant_ids(kwargs.get("participant_ids"))
    if err:
        return err
    targets, err = resolve_participant_users(account_ids, current_user=current_user)
    if err:
        return err
    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err
        if (membership := validate_user_in_call(call, current_user)):
            return membership
        # Lock every known/current/new account in one deterministic order before
        # the Call row. This avoids cross-call add/invite deadlocks where two
        # conferences attempt to acquire each other's participants.
        known_users = users_for_call(call.name)
        lock_users_for_call(*known_users, *targets)
        lock_call_row(call.name)
        lock_participant_rows(call.name)
        call = _reload(call.name)
        current = participant_for_user(call.name, current_user, for_update=True)
        if call.call_mode not in {"direct", "group"} or call.status != "ongoing" or not current or current.status != "joined":
            return fail("Participants can only be added by someone currently in an ongoing call.", error="INVALID_STATE")
        existing = participant_user_set(call.name)
        requested_existing = frappe.get_all(
            "AOS Call Participant",
            filters={"call": call.name, "user": ["in", targets]},
            fields=["user", "added_by"],
            limit=32,
        )
        if requested_existing:
            existing_by_user = {row.user: row for row in requested_existing}
            if (
                len(existing_by_user) == len(targets)
                and all(existing_by_user[target].added_by == current_user for target in targets)
            ):
                return ok("Participants are already invited.", data=_response(call, current_user))
            return fail("One or more accounts are already in this call.", error="VALIDATION_ERROR")
        if len(existing) + len(targets) > 32:
            return fail("A group call supports at most 32 participants.", error="VALIDATION_ERROR")
        if active_call_names_for_users(*targets):
            return fail("A participant is already in another call.", error="ACTIVE_CALL_EXISTS", http_status=409)
        for target in targets:
            if (fanout_rl := rate_limit(
                key=rate_limit_key("calls", "invite_fanout", "user", current_user),
                ttl_seconds=60,
                limit=CALL_INVITEE_FANOUT_LIMIT_PER_MINUTE_PER_USER,
                message="Too many call invitations. Please try again shortly.",
            )):
                return fanout_rl
            if (interaction_error := ensure_interaction_allowed(current_user=current_user, peer_user=target, action="invite to a call")):
                return interaction_error
            if (target_rl := rate_limit(key=rate_limit_key("calls", "initiate", "target", target), ttl_seconds=60, limit=INCOMING_CALL_LIMIT_PER_MINUTE_PER_TARGET, message="One or more accounts are receiving too many call attempts. Please try again shortly.")):
                return target_rl
        now = now_datetime()
        if call.call_mode == "direct":
            # Provider rooms are created with conference capacity from the start,
            # so promotion is a durable application-state transition only; no
            # RTC room recreation or disconnect is required.
            frappe.db.sql(
                "UPDATE `tabAOS Call` SET call_mode='group',max_participants=32,conversation=NULL,video_upgrade_status='none',video_upgrade_requested_by=NULL,video_upgrade_requested_at=NULL,video_upgrade_responded_at=NULL "
                "WHERE name=%s AND call_mode='direct' AND status='ongoing'",
                (call.name,),
            )
            if frappe.db._cursor.rowcount == 0:
                return fail("Call cannot be promoted to a group call.", error="INVALID_STATE")
        for target in targets:
            create_participant(call_name=call.name, user=target, role="participant", status="invited", added_by=current_user, invited_at=now)
        set_participant_count(call.name)
        frappe.db.sql("UPDATE `tabAOS Call` SET state_version=state_version+1 WHERE name=%s", (call.name,))
        call = _reload(call.name)
        publish_participants_invited(call, actor=current_user)
        for target in targets:
            _dispatch_invite(call, target=target, actor=current_user, now=now)
        return ok("Participants invited.", data=_response(_reload(call.name), current_user))
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Add Call Participants Failed")
        return fail("Failed to add call participants.", error="INTERNAL_ERROR")


def request_video_upgrade_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "request_video", current_user), ttl_seconds=60, limit=REQUEST_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER, message="Too many video upgrade requests. Please try again shortly.")):
        return rl
    call_id = kwargs.get("call_id")
    if not call_id:
        return fail("call_id is required.", error="VALIDATION_ERROR")
    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err
        if (membership := validate_user_in_call(call, current_user)):
            return membership
        lock_call_row(call.name)
        call = _reload(call.name)
        row = participant_for_user(call.name, current_user)
        if call.call_mode != "direct":
            return fail("Group calls must choose audio or video when started.", error="INVALID_STATE")
        if call.status != "ongoing" or call.call_type != "audio" or not row or row.status != "joined":
            return fail("Video upgrade can only be requested during an ongoing direct audio call.", error="INVALID_STATE")
        if (call.video_upgrade_status or "none") == "requested":
            return ok("Video upgrade is already requested.", data=_response(call, current_user)) if call.video_upgrade_requested_by == current_user else fail("A video upgrade request is already pending.", error="INVALID_STATE")
        now = now_datetime()
        frappe.db.sql("UPDATE `tabAOS Call` SET video_upgrade_status='requested', video_upgrade_requested_by=%s, video_upgrade_requested_at=%s, video_upgrade_responded_at=NULL, state_version=state_version+1 WHERE name=%s AND status='ongoing' AND call_mode='direct' AND call_type='audio' AND IFNULL(video_upgrade_status,'none')!='requested'", (current_user, now, call.name))
        if frappe.db._cursor.rowcount == 0:
            return fail("Video upgrade cannot be requested.", error="INVALID_STATE")
        call = _reload(call.name)
        publish_video_upgrade_requested(call)
        return ok("Video upgrade requested.", data=_response(call, current_user))
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Request Video Upgrade Failed")
        return fail("Failed to request video upgrade.", error="INTERNAL_ERROR")


def respond_video_upgrade_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "respond_video", current_user), ttl_seconds=60, limit=RESPOND_VIDEO_UPGRADE_LIMIT_PER_MINUTE_PER_USER, message="Too many video upgrade responses. Please try again shortly.")):
        return rl
    call_id = kwargs.get("call_id")
    action = str(kwargs.get("action") or "").strip().lower()
    action = "accepted" if action in {"accept", "accepted"} else "declined" if action in {"decline", "declined", "reject", "rejected"} else action
    if not call_id or action not in {"accepted", "declined"}:
        return fail("call_id and a valid action are required.", error="VALIDATION_ERROR")
    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err
        if (membership := validate_user_in_call(call, current_user)):
            return membership
        lock_call_row(call.name)
        call = _reload(call.name)
        row = participant_for_user(call.name, current_user)
        if call.call_mode != "direct" or call.status != "ongoing" or call.call_type != "audio" or call.video_upgrade_status != "requested" or not row or row.status != "joined":
            return fail("No direct-call video upgrade is awaiting your response.", error="INVALID_STATE")
        if call.video_upgrade_requested_by == current_user:
            return fail("You cannot respond to your own video upgrade request.", error="PERMISSION_DENIED")
        now = now_datetime()
        if action == "accepted":
            frappe.db.sql("UPDATE `tabAOS Call` SET call_type='video', video_upgrade_status='accepted', video_upgrade_responded_at=%s, state_version=state_version+1 WHERE name=%s AND video_upgrade_status='requested' AND video_upgrade_requested_by!=%s", (now, call.name, current_user))
            call = _reload(call.name)
            publish_video_upgrade_accepted(call, actor=current_user)
            return ok("Video upgrade accepted.", data=_response(call, current_user))
        frappe.db.sql("UPDATE `tabAOS Call` SET video_upgrade_status='declined', video_upgrade_responded_at=%s, state_version=state_version+1 WHERE name=%s AND video_upgrade_status='requested' AND video_upgrade_requested_by!=%s", (now, call.name, current_user))
        call = _reload(call.name)
        publish_video_upgrade_declined(call, actor=current_user)
        return ok("Video upgrade declined.", data=_response(call, current_user))
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Respond Video Upgrade Failed")
        return fail("Failed to respond to video upgrade.", error="INTERNAL_ERROR")
