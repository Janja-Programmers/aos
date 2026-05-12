"""
Call Token APIs (implementation).

Handles:
- get_call_token

Used for:
- reconnect
- retry join
- recovering from LiveKit disconnects
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from aos.services.livekit_service import LiveKitService

from .constants import GET_TOKEN_LIMIT_PER_MINUTE_PER_USER

from .validators import (
    validate_call_exists,
    validate_user_in_call,
    validate_call_active,
)

from .realtime import serialize_call_for_realtime


# Helpers
def _get_call_role(call, current_user: str) -> str:
    if current_user == call.caller:
        return "caller"

    return "receiver"


def _build_token_response(
    *,
    call,
    current_user: str,
    token: str,
) -> dict:
    """
    Build reconnect token response.

    Includes the same rich call payload shape used by the rest of call APIs,
    then adds LiveKit connection data.
    """

    data = serialize_call_for_realtime(
        call,
        current_user=current_user,
    )

    data.update(
        {
            "token": token,
            "ws_url": LiveKitService.get_ws_url(),
        }
    )

    return data


# GET CALL TOKEN
def get_call_token_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:calls:token:user:{current_user}",
        ttl_seconds=60,
        limit=GET_TOKEN_LIMIT_PER_MINUTE_PER_USER,
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

        err = validate_call_active(call)
        if err:
            return err

        role = _get_call_role(call, current_user)

        token = LiveKitService.generate_call_token(
            user=current_user,
            room_name=call.room_name,
            metadata=LiveKitService.build_metadata(
                user=current_user,
                role=role,
                conversation=call.conversation,
                call_id=call.name,
                call_type=call.call_type,
            ),
        )

        return ok(
            "Token generated.",
            data=_build_token_response(
                call=call,
                current_user=current_user,
                token=token,
            ),
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Call Token Failed",
        )
        return fail("Failed to generate token.", code="INTERNAL_ERROR")
