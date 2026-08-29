"""
Live Token APIs (implementation).

Handles:
- get_live_token
- get_live_cohost_token

Rules:
- Login is required.
- Hosts can refresh host tokens without a viewer session.
- Non-host viewers must own an active viewer session.
- Co-host tokens are issued only to the workflow candidate.
- Co-host workflow status must be accepted or active.
- The supplied session_id must match the co-host workflow.
- The candidate must still own an active viewer session.
- Token refresh preserves the existing LiveKit participant identity.
- Guests obtain their initial viewer token through join_live.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.validators import require_id
from aos.services.livekit_service import LiveKitService
from aos.services.live.repository import LiveRepository
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.live.livekit import participant_identity, participant_metadata

from .constants import (
    GET_LIVE_COHOST_TOKEN_LIMIT_PER_MINUTE_PER_USER,
    GET_LIVE_TOKEN_LIMIT_PER_MINUTE_PER_USER,
)
from .serializers import (
    get_user_display,
    serialize_live,
    serialize_live_cohost,
)
from .validators import (
    validate_active_view_session,
    validate_cohost_exists,
    validate_live_active,
    validate_live_exists,
    validate_live_social_access,
)


HOST_ROLE = "host"
COHOST_ROLE = "cohost"
VIEWER_ROLE = "viewer"

COHOST_STATUS_ACCEPTED = "accepted"
COHOST_STATUS_ACTIVE = "active"

COHOST_TOKEN_STATUSES = {
    COHOST_STATUS_ACCEPTED,
    COHOST_STATUS_ACTIVE,
}


# GENERIC HELPERS
def _normalize_session_id(
    value,
) -> str | None:
    session_id = str(
        value or ""
    ).strip()

    return session_id or None


def _get_viewer_livekit_identity(
    *,
    live_id: str,
    user: str,
    session_id: str,
) -> str:
    return participant_identity(
        live_id=live_id,
        role=VIEWER_ROLE,
        user=user,
        session_id=session_id,
    )


def _get_livekit_identity(
    *,
    live_id: str,
    user: str,
    role: str,
    session_id: str | None,
) -> str:
    if role not in {HOST_ROLE, VIEWER_ROLE, COHOST_ROLE}:
        frappe.throw("Invalid live token role.")
    if role != HOST_ROLE and not session_id:
        frappe.throw("Session id is required.")
    return participant_identity(
        live_id=live_id,
        role=role,
        user=user,
        session_id=session_id,
    )


# STANDARD TOKEN VALIDATION
def _validate_token_session(
    *,
    live,
    user: str,
    session_id: str | None,
):
    """
    Validate normal host/viewer token refresh eligibility.

    Host:
    - Does not require an AOS Live Stream View row.

    Viewer:
    - Must provide session_id.
    - Must own an active view session for the live.
    """
    if live.host_user == user:
        return None

    blocked_err = validate_live_social_access(live=live, user=user, lock_relationship=True)
    if blocked_err:
        return blocked_err

    if not session_id:
        return fail(
            "session_id is required for viewers.",
            error="VALIDATION_ERROR",
        )

    _, err = validate_active_view_session(
        live.name,
        user,
        session_id,
    )

    return err


# CO-HOST TOKEN VALIDATION
def _validate_cohost_token_eligibility(
    *,
    cohost,
    live,
    user: str,
    session_id: str | None,
):
    """
    Validate whether the authenticated user may receive a co-host token.
    """
    if cohost.live_stream != live.name:
        return fail(
            "Co-host workflow does not belong to this live stream.",
            error="INVALID_STATE",
        )

    if cohost.user != user:
        return fail(
            "Only the co-host candidate can request this token.",
            error="PERMISSION_DENIED",
        )

    blocked_err = validate_live_social_access(
        live=live,
        user=user,
        lock_relationship=True,
    )
    if blocked_err:
        return blocked_err

    if cohost.status not in COHOST_TOKEN_STATUSES:
        return fail(
            "The co-host workflow must be accepted before a token "
            "can be generated.",
            error="INVALID_STATE",
        )

    if not session_id:
        return fail(
            "session_id is required.",
            error="VALIDATION_ERROR",
        )

    if cohost.session_id != session_id:
        return fail(
            "The supplied session does not match the co-host workflow.",
            error="PERMISSION_DENIED",
        )

    _, err = validate_active_view_session(
        live.name,
        user,
        session_id,
    )
    if err:
        return err

    expected_identity = (
        _get_viewer_livekit_identity(
            live_id=live.name,
            user=user,
            session_id=session_id,
        )
    )

    if (
        not cohost.livekit_identity
        or cohost.livekit_identity
        != expected_identity
    ):
        return fail(
            "The co-host LiveKit identity is invalid.",
            error="INVALID_STATE",
        )

    return None


# LIVEKIT PAYLOAD
def _build_livekit_payload(
    *,
    live,
    user: str,
    role: str,
    session_id: str | None,
    cohost_id: str | None = None,
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

    metadata = frappe.as_json(
        participant_metadata(
            role=role,
            user=user,
            display_name=display.get("display_name"),
            avatar=display.get("avatar"),
            is_guest=False,
        )
    )

    token = LiveKitService.generate_live_token(
        user=identity,
        room_name=live.room_name,
        role=role,
        participant_name=display.get(
            "display_name"
        ),
        metadata=metadata,
    )

    return {
        "live_id": live.name,
        "room_name": live.room_name,
        "token": token,
        "ws_url": LiveKitService.get_ws_url(),
        "role": role,
        "identity": identity,
        "user": public_account_id_for_user(user),
        "is_guest": False,
        "session_id": session_id,
    }


# GET STANDARD LIVE TOKEN
def get_live_token_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("live", "token", "user", user),
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
        # Share-lock token issuance against lifecycle termination. Many viewers
        # may obtain tokens concurrently, while end_live's exclusive lock waits
        # until all accepted issuance decisions finish.
        locked_live = LiveRepository().lock_live_shared(live_id)
        if not locked_live:
            return fail("Live stream not found.", error="NOT_FOUND")

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
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            "Live operation failed.",
            "Get Live Token Failed",
        )
        return fail(
            "Failed to generate token.",
            error="INTERNAL_ERROR",
        )


# GET CO-HOST TOKEN
def get_live_cohost_token_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("live", "cohost", "token", "user", user),
        ttl_seconds=60,
        limit=(
            GET_LIVE_COHOST_TOKEN_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many co-host token requests.",
    )
    if rl:
        return rl

    cohost_id, err = require_id(
        kwargs.get("cohost_id"),
        "cohost_id",
    )
    if err:
        return err

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    try:
        # Resolve the parent first, then lock in deterministic parent->child
        # order. Co-host cancellation/end uses the same order, preventing a
        # publishing token from being minted concurrently with demotion.
        cohost, err = validate_cohost_exists(
            cohost_id
        )
        if err:
            return err

        locked_live = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Live Stream`
            WHERE name = %s
            LIMIT 1
            FOR UPDATE
            """,
            (cohost.live_stream,),
            as_dict=True,
        )
        if not locked_live:
            return fail("Live stream not found.", error="NOT_FOUND")

        locked_cohost = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Live CoHost`
            WHERE name = %s AND live_stream = %s
            LIMIT 1
            FOR UPDATE
            """,
            (cohost_id, cohost.live_stream),
            as_dict=True,
        )
        if not locked_cohost:
            return fail("Co-host request not found.", error="NOT_FOUND")

        # Re-read after acquiring both locks so no pre-lock status or session
        # value can authorize the token.
        cohost, err = validate_cohost_exists(cohost_id)
        if err:
            return err
        live, err = validate_live_exists(cohost.live_stream)
        if err:
            return err

        err = validate_live_active(
            live
        )
        if err:
            return err

        err = _validate_cohost_token_eligibility(
            cohost=cohost,
            live=live,
            user=user,
            session_id=session_id,
        )
        if err:
            return err

        session = _build_livekit_payload(
            live=live,
            user=user,
            role=COHOST_ROLE,
            session_id=session_id,
            cohost_id=cohost.name,
        )

        # Defensive equality check between the workflow identity and the
        # newly generated token identity.
        if (
            session["identity"]
            != cohost.livekit_identity
        ):
            return fail(
                "Generated identity does not match the co-host workflow.",
                error="INVALID_STATE",
            )

        return ok(
            "Co-host token generated.",
            data={
                **session,
                "cohost": serialize_live_cohost(
                    cohost,
                    include_internal=True,
                ),
                "live": serialize_live(
                    live,
                    viewer=user,
                    session_id=session_id,
                ),
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            "Live operation failed.",
            "Get Live CoHost Token Failed",
        )
        return fail(
            "Failed to generate co-host token.",
            error="INTERNAL_ERROR",
        )
