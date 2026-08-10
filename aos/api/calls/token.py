"""Call reconnect-token API implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.api.shared.user_display import get_user_display
from aos.services.livekit_service import LiveKitService
from aos.services.calls.errors import CallError
from aos.services.calls.livekit import get_call_ws_url, issue_call_token, participant_identity
from aos.services.calls.policy import (
    ensure_call_interaction_allowed,
    lock_call_row,
    lock_users_for_call,
)
from aos.services.calls.reconciliation import clear_missing_room_marker

from .constants import GET_TOKEN_LIMIT_PER_MINUTE_PER_USER
from .validators import validate_call_exists, validate_user_in_call, validate_call_active
from .realtime import serialize_call_for_realtime


def _get_call_role(call, current_user: str) -> str:
    return "caller" if current_user == call.caller else "receiver"


def _get_user_display(user: str | None) -> dict:
    display = get_user_display(user)
    return {"display_name": display.get("display_name"), "avatar": display.get("avatar")}


def _build_call_metadata(*, user: str, role: str, conversation: str, call_id: str, call_type: str) -> str:
    display = _get_user_display(user)
    return LiveKitService.build_metadata(
        user=participant_identity(user),
        role=role,
        conversation=conversation,
        call_id=call_id,
        call_type=call_type,
        display_name=display.get("display_name"),
        avatar=display.get("avatar"),
        is_guest=False,
    )


def _build_token_response(*, call, current_user: str, token: str) -> dict:
    data = serialize_call_for_realtime(call, current_user=current_user)
    data.update({"token": token, "ws_url": get_call_ws_url()})
    return data


def get_call_token_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("calls", "token", current_user),
        ttl_seconds=60,
        limit=GET_TOKEN_LIMIT_PER_MINUTE_PER_USER,
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

        # Serialize with Social/Accounts policy changes and terminal state
        # transitions. Both participant and call locks are deterministic.
        lock_users_for_call(call.caller, call.receiver)
        lock_call_row(call_id)
        call, err = validate_call_exists(call_id)
        if err:
            return err
        err = validate_user_in_call(call, current_user)
        if err:
            return err
        err = validate_call_active(call)
        if err:
            return err

        interaction_error = ensure_call_interaction_allowed(
            call, current_user, action="join a call with"
        )
        if interaction_error:
            return interaction_error

        # Caller may join while establishing. Receiver receives a token during
        # accept, or through this endpoint only after the call is ongoing.
        if current_user == call.receiver and call.status != "ongoing":
            return fail(
                "Call must be accepted before joining.",
                error="INVALID_STATE",
                http_status=409,
            )

        role = _get_call_role(call, current_user)
        if call.status == "ongoing":
            # Protected by the call-row lock and rolled back if token issuance
            # fails; this makes an authorized reconnect beat recovery cleanup.
            clear_missing_room_marker(call.name)

        token = issue_call_token(
            identity=participant_identity(current_user),
            room_name=call.room_name,
            metadata=_build_call_metadata(
                user=current_user,
                role=role,
                conversation=call.conversation,
                call_id=call.name,
                call_type=call.call_type,
            ),
        )

        return ok(
            "Token generated.",
            data=_build_token_response(call=call, current_user=current_user, token=token),
        )
    except CallError:
        raise
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Get Call Token Failed")
        return fail("Failed to generate token.", error="INTERNAL_ERROR")
