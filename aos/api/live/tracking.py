"""
Live Tracking APIs (implementation).

Handles:
- track_join
- track_leave

Rules:
- Guests can watch live streams.
- session_id is required for both guest and authenticated viewers.
- user is optional and only set for authenticated viewers.
- Viewer count is derived from DB (AOS Live Stream View).
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

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

from .realtime import (
    publish_viewer_count,
    publish_viewer_joined,
    publish_viewer_left,
)

from .serializers import (
    is_guest_user,
    serialize_live,
)


# HELPERS
def _viewer_user() -> str | None:
    user = current_user()
    return None if is_guest_user(user) else user


def _normalize_session_id(value) -> str | None:
    session_id = (value or "").strip()
    return session_id or None


def _get_viewer_count(live_id: str) -> int:
    """
    Single source of truth for viewer count.
    """
    result = frappe.db.sql(
        """
        SELECT COUNT(*) AS count
        FROM `tabAOS Live Stream View`
        WHERE live_stream = %s
          AND is_active = 1
        """,
        (live_id,),
        as_dict=True,
    )

    return int(result[0].get("count") or 0)


def _get_total_views(live_id: str) -> int:
    """
    Total sessions ever created for this live.
    """
    return frappe.db.count(
        "AOS Live Stream View",
        filters={"live_stream": live_id},
    )


def _update_live_view_metrics(live_id: str) -> int:
    """
    Recompute lightweight view counters from DB.

    viewer_count = active view sessions
    total_views = all view sessions
    peak_viewers = max(previous peak, current viewer_count)
    """
    viewer_count = _get_viewer_count(live_id)
    total_views = _get_total_views(live_id)

    current_peak = frappe.db.get_value(
        "AOS Live Stream",
        live_id,
        "peak_viewers",
    ) or 0

    peak_viewers = max(int(current_peak or 0), viewer_count)

    frappe.db.set_value(
        "AOS Live Stream",
        live_id,
        {
            "viewer_count": viewer_count,
            "total_views": total_views,
            "peak_viewers": peak_viewers,
        },
        update_modified=False,
    )

    return viewer_count


def _get_active_view_by_session(live_id: str, session_id: str):
    return frappe.db.get_value(
        "AOS Live Stream View",
        {
            "live_stream": live_id,
            "session_id": session_id,
            "is_active": 1,
        },
        ["name"],
        as_dict=True,
    )


# TRACK JOIN
def track_join_impl(**kwargs):
    viewer = _viewer_user()
    session_id = _normalize_session_id(kwargs.get("session_id"))

    err = validate_view_identity(viewer, session_id)
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:track_join:ip:{request_ip()}",
        ttl_seconds=60,
        limit=TRACK_JOIN_LIMIT_PER_MINUTE_PER_IP,
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

        # Idempotent join by session_id.
        existing = _get_active_view_by_session(live_id, session_id)

        if existing:
            viewer_count = _update_live_view_metrics(live_id)
            publish_viewer_count(live_id, viewer_count)

            live.reload()

            return ok(
                "Already joined.",
                data={
                    "view_id": existing.name,
                    "viewer_count": viewer_count,
                    "live": serialize_live(
                        live,
                        viewer=viewer,
                        session_id=session_id,
                    ),
                },
            )

        view = frappe.new_doc("AOS Live Stream View")
        view.live_stream = live_id
        view.user = viewer
        view.session_id = session_id
        view.joined_at = now_datetime()
        view.insert(ignore_permissions=True)

        viewer_count = _update_live_view_metrics(live_id)

        publish_viewer_count(live_id, viewer_count)
        publish_viewer_joined(
            live_id=live_id,
            user=viewer,
            session_id=session_id,
        )

        live.reload()

        return ok(
            "Joined live session.",
            data={
                "view_id": view.name,
                "viewer_count": viewer_count,
                "live": serialize_live(
                    live,
                    viewer=viewer,
                    session_id=session_id,
                ),
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Track Join Failed")
        frappe.db.rollback()
        return fail("Failed to track join.", code="INTERNAL_ERROR")


# TRACK LEAVE
def track_leave_impl(**kwargs):
    viewer = _viewer_user()
    session_id = _normalize_session_id(kwargs.get("session_id"))

    err = validate_view_identity(viewer, session_id)
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:track_leave:ip:{request_ip()}",
        ttl_seconds=60,
        limit=TRACK_LEAVE_LIMIT_PER_MINUTE_PER_IP,
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

        view_row, err = validate_active_view_session(
            live_id,
            viewer,
            session_id,
        )
        if err:
            return err

        view = frappe.get_doc("AOS Live Stream View", view_row.name)
        view.left_at = now_datetime()
        view.save(ignore_permissions=True)

        viewer_count = _update_live_view_metrics(live_id)

        publish_viewer_count(live_id, viewer_count)
        publish_viewer_left(
            live_id=live_id,
            user=viewer,
            session_id=session_id,
        )

        live.reload()

        return ok(
            "Left live session.",
            data={
                "view_id": view.name,
                "viewer_count": viewer_count,
                "live": serialize_live(
                    live,
                    viewer=viewer,
                    session_id=session_id,
                ),
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Track Leave Failed")
        frappe.db.rollback()
        return fail("Failed to track leave.", code="INTERNAL_ERROR")
