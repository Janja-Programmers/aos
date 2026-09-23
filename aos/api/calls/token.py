"""Call reconnect-token API for direct/group participants."""
from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.api.shared.user_display import get_user_display
from aos.services.calls.errors import CallError
from aos.services.calls.identifiers import public_call_id
from aos.services.calls.livekit import ensure_call_join_ready, get_call_ws_url, issue_call_token, participant_identity
from aos.services.calls.participants import participant_for_user
from aos.services.calls.policy import ensure_call_interaction_allowed, lock_call_row
from aos.services.calls.reconciliation import clear_missing_room_marker
from aos.services.livekit_service import LiveKitService

from .constants import GET_TOKEN_LIMIT_PER_MINUTE_PER_USER
from .realtime import serialize_call_for_realtime
from .validators import validate_call_active, validate_call_exists, validate_user_in_call


def _metadata(call, user: str, role: str) -> str:
    display = get_user_display(user)
    return LiveKitService.build_metadata(
        user=participant_identity(user), role=role, conversation=call.conversation or "",
        call_id=public_call_id(call), call_type=call.call_type,
        display_name=display.get("display_name"), avatar=display.get("avatar"), is_guest=False,
    )


def get_call_token_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "token", current_user), ttl_seconds=60, limit=GET_TOKEN_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")):
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
        call = frappe.get_doc("AOS Call", call.name)
        if (state_error := validate_call_active(call)):
            return state_error
        participant = participant_for_user(call.name, current_user, for_update=True)
        if not participant or participant.status != "joined":
            return fail("Call must be accepted before joining.", error="INVALID_STATE", http_status=409)
        ensure_call_join_ready(call)
        if (policy_error := ensure_call_interaction_allowed(call, current_user, action="join a call with")):
            return policy_error
        if call.status == "ongoing":
            clear_missing_room_marker(call.name)
        token = issue_call_token(
            identity=participant_identity(current_user), room_name=call.room_name, call_type=call.call_type,
            metadata=_metadata(call, current_user, participant.role),
        )
        data = serialize_call_for_realtime(call, current_user=current_user)
        data.update({"token": token, "ws_url": get_call_ws_url()})
        return ok("Token generated.", data=data)
    except CallError:
        raise
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Get Call Token Failed")
        return fail("Failed to generate token.", error="INTERNAL_ERROR")
