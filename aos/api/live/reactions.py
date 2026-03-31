"""
Live Reaction APIs (implementation).

Handles:
- send_reaction
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail

from .constants import (
    SEND_REACTION_LIMIT_PER_MINUTE_PER_IP,
)

from .validators import (
    validate_live_exists,
    validate_live_active,
    validate_view_identity,
)

from .realtime import publish_reaction


# HELPERS
def _get_identity(kwargs):
    user = current_user()
    session_id = kwargs.get("session_id")

    if not user:
        session_id = session_id or request_ip()

    return user, session_id


# SEND REACTION
def send_reaction_impl(**kwargs):
    user, session_id = _get_identity(kwargs)

    rl = rate_limit(
        key=f"aos:live:reaction:{user or session_id}",
        ttl_seconds=60,
        limit=SEND_REACTION_LIMIT_PER_MINUTE_PER_IP,
        message="Too many reactions. Please slow down.",
    )
    if rl:
        return rl

    live_id = kwargs.get("live_id")
    reaction_type = (kwargs.get("reaction_type") or "").strip()

    if not live_id:
        return fail("live_id is required.", code="VALIDATION_ERROR")

    if not reaction_type:
        return fail("reaction_type is required.", code="VALIDATION_ERROR")

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_live_active(live)
        if err:
            return err

        err = validate_view_identity(user, session_id)
        if err:
            return err

        # INSERT REACTION (event-based)
        reaction = frappe.new_doc("AOS Live Stream Reaction")
        reaction.live_stream = live_id
        reaction.user = user
        reaction.session_id = session_id
        reaction.reaction_type = reaction_type
        reaction.insert(ignore_permissions=True)

        # REALTIME (batched in realtime.py)
        publish_reaction(live_id, reaction_type)

        return ok("Reaction sent.")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Send Reaction Failed")
        frappe.db.rollback()
        return fail("Failed to send reaction.", code="INTERNAL_ERROR")
