"""
Live Token APIs (implementation).

Handles:
- get_live_token

Notes:
- Requires login.
- Guests should use join_live with session_id if guest watching is allowed.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from aos.services.livekit_service import LiveKitService

from .constants import (
    GET_LIVE_TOKEN_LIMIT_PER_MINUTE_PER_USER,
)

from .validators import (
    validate_live_exists,
    validate_live_active,
)

from .serializers import (
    get_user_display,
    serialize_live,
)


# GET LIVE TOKEN
def get_live_token_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:token:user:{user}",
        ttl_seconds=60,
        limit=GET_LIVE_TOKEN_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_live_active(live)
        if err:
            return err

        role = "host" if live.host_user == user else "viewer"
        display = get_user_display(user)
        session_id = kwargs.get("session_id")

        metadata = LiveKitService.build_metadata(
            user=user,
            role=role,
            display_name=display.get("display_name"),
            avatar=display.get("avatar"),
            is_guest=False,
            session_id=session_id,
        )

        token = LiveKitService.generate_live_token(
            user=user,
            room_name=live.room_name,
            role=role,
            metadata=metadata,
        )

        return ok(
            "Token generated.",
            data={
                "live_id": live.name,
                "room_name": live.room_name,
                "token": token,
                "ws_url": LiveKitService.get_ws_url(),
                "role": role,
                "identity": user,
                "is_guest": False,
                "live": serialize_live(
                    live,
                    viewer=user,
                    session_id=session_id,
                ),
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Get Live Token Failed")
        frappe.db.rollback()
        return fail("Failed to generate token.", code="INTERNAL_ERROR")
