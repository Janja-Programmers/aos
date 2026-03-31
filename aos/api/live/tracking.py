"""
Live Tracking APIs (implementation).

Handles:
- track_join
- track_leave

IMPORTANT:
- Viewer count is derived from DB (AOS Live Stream View)
- No increment/decrement counters
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail

from .constants import (
    TRACK_JOIN_LIMIT_PER_MINUTE_PER_IP,
    TRACK_LEAVE_LIMIT_PER_MINUTE_PER_IP,
)

from .validators import (
    validate_live_exists,
    validate_live_active,
    validate_view_identity,
    validate_active_view_session,
)

from .realtime import publish_viewer_count


# HELPERS
def _get_identity(kwargs):
    """
    Identity resolution strategy:

    1. Authenticated user → use user
    2. Guest → require session_id (frontend must send)
    3. Fallback → IP (last resort, not ideal)
    """
    user = current_user()
    session_id = kwargs.get("session_id")

    if not user:
        session_id = session_id or request_ip()

    return user, session_id


def _get_viewer_count(live_id: str) -> int:
    """
    Single source of truth for viewer count.
    """
    result = frappe.db.sql(
        """
        SELECT COUNT(*) as count
        FROM `tabAOS Live Stream View`
        WHERE live_stream = %s
          AND is_active = 1
        """,
        (live_id,),
        as_dict=True,
    )
    return int(result[0].get("count") or 0)


# TRACK JOIN
def track_join_impl(**kwargs):
    user, session_id = _get_identity(kwargs)

    rl = rate_limit(
        key=f"aos:live:track_join:{user or session_id}",
        ttl_seconds=60,
        limit=TRACK_JOIN_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_id = kwargs.get("live_id")
    if not live_id:
        return fail("live_id is required.", code="VALIDATION_ERROR")

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

        # IDEMPOTENT JOIN
        existing = frappe.db.get_value(
            "AOS Live Stream View",
            {
                "live_stream": live_id,
                "is_active": 1,
                "user": user,
                "session_id": session_id,
            },
            ["name"],
            as_dict=True,
        )

        if existing:
            return ok("Already joined.", data={"view_id": existing.name})

        # CREATE SESSION
        view = frappe.new_doc("AOS Live Stream View")
        view.live_stream = live_id
        view.user = user
        view.session_id = session_id
        view.joined_at = now_datetime()
        view.insert(ignore_permissions=True)

        # REALTIME COUNT
        viewer_count = _get_viewer_count(live_id)
        publish_viewer_count(live_id, viewer_count)

        return ok(
            "Joined live session.",
            data={"view_id": view.name},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Track Join Failed")
        frappe.db.rollback()
        return fail("Failed to track join.", code="INTERNAL_ERROR")


# TRACK LEAVE
def track_leave_impl(**kwargs):
    user, session_id = _get_identity(kwargs)

    rl = rate_limit(
        key=f"aos:live:track_leave:{user or session_id}",
        ttl_seconds=60,
        limit=TRACK_LEAVE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_id = kwargs.get("live_id")
    if not live_id:
        return fail("live_id is required.", code="VALIDATION_ERROR")

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_view_identity(user, session_id)
        if err:
            return err

        view_row, err = validate_active_view_session(live_id, user, session_id)
        if err:
            return err

        now = now_datetime()

        frappe.db.set_value(
            "AOS Live Stream View",
            view_row.name,
            {
                "left_at": now,
                "is_active": 0,
            },
            update_modified=False,
        )

        # REALTIME COUNT
        viewer_count = _get_viewer_count(live_id)
        publish_viewer_count(live_id, viewer_count)

        return ok("Left live session.")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Track Leave Failed")
        frappe.db.rollback()
        return fail("Failed to track leave.", code="INTERNAL_ERROR")
