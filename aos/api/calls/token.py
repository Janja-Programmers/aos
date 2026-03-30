"""
Call Token APIs (implementation).

Handles:
- get_call_token (reconnect / retry)
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


# GET CALL TOKEN (RECONNECT)
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

        # Determine role
        if current_user == call.caller:
            role = "caller"
        else:
            role = "receiver"

        # Generate token
        token = LiveKitService.generate_call_token(
            user=current_user,
            room_name=call.room_name,
            metadata=LiveKitService.build_metadata(
                user=current_user,
                role=role,
                conversation=call.conversation,
                call_id=call.name,
            ),
        )

        return ok(
            "Token generated.",
            data={
                "call_id": call.name,
                "room_name": call.room_name,
                "token": token,
                "ws_url": LiveKitService.get_ws_url(),
                "status": call.status,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Call Token Failed",
        )
        return fail("Failed to generate token.", code="INTERNAL_ERROR")
