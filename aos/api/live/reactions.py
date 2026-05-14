"""
Live Reaction APIs (implementation).

Handles:
- send_reaction

Rules:
- Guests can watch live.
- Only logged-in users can react.
- Reactions are event-based and tied to User.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from .constants import (
    SEND_REACTION_LIMIT_PER_MINUTE_PER_USER,
)

from .validators import (
    validate_live_exists,
    validate_live_active,
)

from .realtime import publish_reaction


VALID_REACTION_TYPES = {
    "like",
    "fire",
    "clap",
    "love",
    "wow",
}


# SEND REACTION
def send_reaction_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:reaction:user:{user}",
        ttl_seconds=60,
        limit=SEND_REACTION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many reactions. Please slow down.",
    )
    if rl:
        return rl

    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    reaction_type = (kwargs.get("reaction_type") or "").strip()

    if not reaction_type:
        return fail("reaction_type is required.", code="VALIDATION_ERROR")

    if reaction_type not in VALID_REACTION_TYPES:
        return fail("Invalid reaction_type.", code="VALIDATION_ERROR")

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_live_active(live)
        if err:
            return err

        reaction = frappe.new_doc("AOS Live Stream Reaction")
        reaction.live_stream = live_id
        reaction.user = user
        reaction.reaction_type = reaction_type
        reaction.insert(ignore_permissions=True)

        publish_reaction(
            live_id=live_id,
            reaction_type=reaction_type,
            user=user,
        )

        return ok(
            "Reaction sent.",
            data={
                "reaction_id": reaction.name,
                "reaction_type": reaction.reaction_type,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Send Reaction Failed")
        frappe.db.rollback()
        return fail("Failed to send reaction.", code="INTERNAL_ERROR")
