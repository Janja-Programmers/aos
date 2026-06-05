"""
Live Token APIs (implementation).

Handles:
- get_live_token

Rules:
- Login is required.
- Hosts can refresh host tokens without a viewer session.
- Non-host users must provide an active session_id for the live.
- The active view session must belong to the authenticated user.
- Refreshed tokens preserve the same LiveKit participant identity originally
  issued by join_live/start_live.
- Guests obtain tokens through join_live.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import require_id
from aos.services.livekit_service import LiveKitService

from .constants import (
    GET_LIVE_TOKEN_LIMIT_PER_MINUTE_PER_USER,
)
from .serializers import (
    get_user_display,
    serialize_live,
)
from .validators import (
    validate_active_view_session,
    validate_live_active,
    validate_live_exists,
)


HOST_ROLE = "host"
VIEWER_ROLE = "viewer"


# HELPERS
def _normalize_session_id(
    value,
) -> str | None:
    session_id = str(
        value or ""
    ).strip()

    return session_id or None


def _get_livekit_identity(
    *,
    live_id: str,
    user: str,
    role: str,
    session_id: str | None,
) -> str:
    """
    Build the same LiveKit identity used by live.py.

    Host:
        user:{user}:host:{live_id}

    Authenticated viewer:
        user:{user}:session:{session_id}

    Token refresh must preserve the participant identity. Generating a token
    with a different identity would create or replace a different LiveKit
    participant instead of refreshing the existing connection.
    """
    if role == HOST_ROLE:
        return (
            f"user:{user}:"
            f"host:{live_id}"
        )

    if not session_id:
        frappe.throw(
            "Session id is required for viewers."
        )

    return (
        f"user:{user}:"
        f"session:{session_id}"
    )


def _validate_token_session(
    *,
    live,
    user: str,
    session_id: str | None,
):
    """
    Validate token-refresh eligibility.

    Host:
    - Does not require an AOS Live Stream View row.

    Viewer:
    - Must provide session_id.
    - Must own an active view session for the live.
    """
    if live.host_user == user:
        return None

    if not session_id:
        return fail(
            "session_id is required for viewers.",
            code="VALIDATION_ERROR",
        )

    _, err = validate_active_view_session(
        live.name,
        user,
        session_id,
    )

    return err


def _build_livekit_payload(
    *,
    live,
    user: str,
    role: str,
    session_id: str | None,
) -> dict:
    identity = _get_livekit_identity(
        live_id=live.name,
        user=user,
        role=role,
        session_id=session_id,
    )

    display = get_user_display(
        user
    )

    metadata = LiveKitService.build_metadata(
        # Keep the stable AOS user separate from the LiveKit identity.
        user=user,
        role=role,
        display_name=display.get(
            "display_name"
        ),
        avatar=display.get(
            "avatar"
        ),
        is_guest=False,
        session_id=session_id,
    )

    token = LiveKitService.generate_live_token(
        user=identity,
        room_name=live.room_name,
        role=role,
        metadata=metadata,
    )

    return {
        "live_id": live.name,
        "room_name": live.room_name,
        "token": token,
        "ws_url": LiveKitService.get_ws_url(),
        "role": role,
        "identity": identity,
        "user": user,
        "is_guest": False,
        "session_id": session_id,
    }


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

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    try:
        live, err = validate_live_exists(
            live_id
        )
        if err:
            return err

        err = validate_live_active(
            live
        )
        if err:
            return err

        err = _validate_token_session(
            live=live,
            user=user,
            session_id=session_id,
        )
        if err:
            return err

        role = (
            HOST_ROLE
            if live.host_user == user
            else VIEWER_ROLE
        )

        session = _build_livekit_payload(
            live=live,
            user=user,
            role=role,
            session_id=session_id,
        )

        return ok(
            "Token generated.",
            data={
                **session,
                "live": serialize_live(
                    live,
                    viewer=user,
                    session_id=session_id,
                ),
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Get Live Token Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to generate token.",
            code="INTERNAL_ERROR",
        )
