"""
Live Reaction APIs (implementation).

Handles:
- send_reaction

Rules:
- Guests can watch live streams but cannot react.
- Only logged-in users can react.
- The live host can react without a viewer session.
- Non-host users must own an active view session for the live.
- Reactions are immutable event records tied to a User.
- Each accepted reaction is published immediately through realtime.
- API and realtime use the same canonical reaction payload.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.validators import require_id
from aos.services.live.repository import LiveRepository

from .constants import (
    SEND_REACTION_LIMIT_PER_MINUTE_PER_USER,
)
from .realtime import publish_reaction
from .serializers import get_user_display
from .validators import (
    validate_live_active,
    validate_live_exists,
    validate_live_participant_session,
)


LIVE_REACTION_DOCTYPE = "AOS Live Stream Reaction"

VALID_REACTION_TYPES = {
    "like",
    "fire",
    "clap",
    "love",
    "wow",
}


# NORMALIZATION
def _normalize_session_id(
    value,
) -> str | None:
    session_id = str(
        value or ""
    ).strip()

    return session_id or None


def _normalize_reaction_type(
    value,
) -> str:
    return str(
        value or ""
    ).strip().lower()


# SERIALIZATION
def _serialize_reaction(
    reaction,
    *,
    user_payload: dict,
) -> dict:
    """
    Build the canonical reaction payload used by both the API response and
    realtime event.
    """
    return {
        "id": reaction.name,
        "reaction_id": reaction.name,
        "live_id": reaction.live_stream,
        "reaction_type": reaction.reaction_type,
        "user": user_payload["user"],
        "display_name": user_payload["display_name"],
        "avatar": user_payload["avatar"],
        "created_at": reaction.creation,
    }


# SEND REACTION
def send_reaction_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("live", "reaction", "user", user),
        ttl_seconds=60,
        limit=SEND_REACTION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many reactions. Please slow down.",
    )
    if rl:
        return rl

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    reaction_type = _normalize_reaction_type(
        kwargs.get("reaction_type")
    )

    if not reaction_type:
        return fail(
            "reaction_type is required.",
            error="VALIDATION_ERROR",
        )

    if reaction_type not in VALID_REACTION_TYPES:
        return fail(
            "Invalid reaction_type.",
            error="VALIDATION_ERROR",
        )

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    try:
        LiveRepository().lock_live(live_id)
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

        err = validate_live_participant_session(
            live=live,
            user=user,
            session_id=session_id,
        )
        if err:
            return err

        reaction = frappe.new_doc(
            LIVE_REACTION_DOCTYPE
        )

        reaction.live_stream = live_id
        reaction.user = user
        reaction.reaction_type = reaction_type

        reaction.insert(
            ignore_permissions=True
        )

        user_payload = get_user_display(
            user
        )

        serialized = _serialize_reaction(
            reaction,
            user_payload=user_payload,
        )

        publish_reaction(
            live_id=live_id,
            reaction=serialized,
        )

        return ok(
            "Reaction sent.",
            data={
                "reaction": serialized,
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            "Live operation failed.",
            "Send Reaction Failed",
        )
        return fail(
            "Failed to send reaction.",
            error="INTERNAL_ERROR",
        )
