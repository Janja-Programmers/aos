"""
Live Tracking APIs (implementation).

Handles:
- track_join
- track_leave
- Viewer count synchronization
- Host-only viewer-joined system messages

Rules:
- Guests can watch live streams.
- session_id is required for guest and authenticated viewers.
- user is optional and only stored for authenticated viewers.
- Viewer count is derived from AOS Live Stream View.
- A viewer-joined system message is created only for a new view session.
- Repeated track_join calls for the same active session are idempotent.
- The host's own tracking session does not create a joined message.
- Viewer identities and session IDs are not broadcast to the live room.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import require_id
from aos.services.live_analytics_service import LiveAnalyticsService

from .constants import (
    TRACK_JOIN_LIMIT_PER_MINUTE_PER_IP,
    TRACK_LEAVE_LIMIT_PER_MINUTE_PER_IP,
)
from .messages import create_live_system_message
from .realtime import (
    publish_live_message_to_user,
    publish_viewer_count,
    publish_viewer_left,
)
from .serializers import (
    get_user_display,
    is_guest_user,
    serialize_live,
)
from .validators import (
    validate_active_view_session,
    validate_live_active,
    validate_live_exists,
    validate_view_identity,
)


LIVE_VIEW_DOCTYPE = "AOS Live Stream View"

GUEST_JOINED_MESSAGE = "A guest joined."


# IDENTITY HELPERS
def _viewer_user() -> str | None:
    user = current_user()

    return None if is_guest_user(user) else user


def _normalize_session_id(value) -> str | None:
    session_id = str(value or "").strip()

    return session_id or None


# VIEW HELPERS
def _get_active_view_by_session(
    *,
    live_id: str,
    session_id: str,
    viewer: str | None,
):
    """
    Fetch an active view session.

    For authenticated viewers, user ownership is included in the lookup.
    Guest sessions are identified by live_stream + session_id.
    """

    filters = {
        "live_stream": live_id,
        "session_id": session_id,
        "is_active": 1,
    }

    if viewer:
        filters["user"] = viewer

    return frappe.db.get_value(
        LIVE_VIEW_DOCTYPE,
        filters,
        [
            "name",
            "user",
            "session_id",
        ],
        as_dict=True,
    )


def _sync_view_metrics(
    live_id: str,
) -> dict:
    """
    Synchronize all derived view counters from the view-session table.

    LiveAnalyticsService is the canonical owner of Live analytics.
    """

    metrics = LiveAnalyticsService.sync_view_metrics(
        live_id=live_id,
    )

    if metrics:
        return metrics

    # Defensive fallback if analytics synchronization unexpectedly fails.
    viewer_count = frappe.db.count(
        LIVE_VIEW_DOCTYPE,
        filters={
            "live_stream": live_id,
            "is_active": 1,
        },
    )

    return {
        "viewer_count": int(viewer_count or 0),
        "total_views": 0,
        "peak_viewers": 0,
        "total_watch_time_seconds": 0,
    }


def _create_view_session(
    *,
    live_id: str,
    viewer: str | None,
    session_id: str,
):
    view = frappe.new_doc(
        LIVE_VIEW_DOCTYPE
    )

    view.live_stream = live_id
    view.user = viewer
    view.session_id = session_id
    view.joined_at = now_datetime()
    view.is_active = 1

    view.insert(ignore_permissions=True)

    return view


# SYSTEM MESSAGE HELPERS
def _build_join_message_content(
    viewer: str | None,
) -> str:
    if not viewer:
        return GUEST_JOINED_MESSAGE

    display = get_user_display(viewer)

    display_name = (
        display.get("display_name")
        or viewer
    )

    return f"{display_name} joined."


def _create_viewer_joined_message(
    *,
    live,
    viewer: str | None,
    session_id: str,
) -> dict | None:
    """
    Persist and publish a host-only viewer-joined system message.

    The host's own view session is excluded.
    """

    if viewer and viewer == live.host_user:
        return None

    message = create_live_system_message(
        live_id=live.name,
        message_type="viewer_joined",
        content=_build_join_message_content(
            viewer
        ),
        user=viewer,
        target_user=viewer,
        metadata={
            "session_id": session_id,
            "is_guest": viewer is None,
        },
        visible_to_host=True,
        visible_to_viewers=False,

        # Host-only messages must not be broadcast to the live room.
        publish=False,
    )

    publish_live_message_to_user(
        user=live.host_user,
        live_id=live.name,
        message=message,
    )

    return message


# TRACK JOIN
def track_join_impl(**kwargs):
    viewer = _viewer_user()

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    err = validate_view_identity(
        viewer,
        session_id,
    )
    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:live:track_join:"
            f"{viewer or session_id or request_ip()}"
        ),
        ttl_seconds=60,
        limit=TRACK_JOIN_LIMIT_PER_MINUTE_PER_IP,
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

    try:
        live, err = validate_live_exists(
            live_id
        )
        if err:
            return err

        err = validate_live_active(live)
        if err:
            return err

        # Idempotent join based on an active session owned by this viewer.
        existing = _get_active_view_by_session(
            live_id=live_id,
            session_id=session_id,
            viewer=viewer,
        )

        if existing:
            metrics = _sync_view_metrics(
                live_id
            )

            viewer_count = int(
                metrics.get("viewer_count") or 0
            )

            publish_viewer_count(
                live_id,
                viewer_count,
            )

            live.reload()

            return ok(
                "Already joined.",
                data={
                    "view_id": existing.name,
                    "viewer_count": viewer_count,
                    "is_new_session": False,
                    "join_message": None,
                    "live": serialize_live(
                        live,
                        viewer=viewer,
                        session_id=session_id,
                    ),
                },
            )

        view = _create_view_session(
            live_id=live_id,
            viewer=viewer,
            session_id=session_id,
        )

        metrics = _sync_view_metrics(
            live_id
        )

        viewer_count = int(
            metrics.get("viewer_count") or 0
        )

        # Room participants only need the updated count.
        publish_viewer_count(
            live_id,
            viewer_count,
        )

        # Viewer identity is sent only to the host as a system message.
        join_message = _create_viewer_joined_message(
            live=live,
            viewer=viewer,
            session_id=session_id,
        )

        live.reload()

        return ok(
            "Joined live session.",
            data={
                "view_id": view.name,
                "viewer_count": viewer_count,
                "is_new_session": True,
                "join_message": join_message,
                "live": serialize_live(
                    live,
                    viewer=viewer,
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
            "Track Join Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to track join.",
            code="INTERNAL_ERROR",
        )


# TRACK LEAVE
def track_leave_impl(**kwargs):
    viewer = _viewer_user()

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    err = validate_view_identity(
        viewer,
        session_id,
    )
    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:live:track_leave:"
            f"{viewer or session_id or request_ip()}"
        ),
        ttl_seconds=60,
        limit=TRACK_LEAVE_LIMIT_PER_MINUTE_PER_IP,
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

    try:
        live, err = validate_live_exists(
            live_id
        )
        if err:
            return err

        view_row, err = validate_active_view_session(
            live_id,
            viewer,
            session_id,
        )
        if err:
            return err

        view = frappe.get_doc(
            LIVE_VIEW_DOCTYPE,
            view_row.name,
        )

        view.left_at = now_datetime()
        view.is_active = 0
        view.save(ignore_permissions=True)

        metrics = _sync_view_metrics(
            live_id
        )

        viewer_count = int(
            metrics.get("viewer_count") or 0
        )

        publish_viewer_count(
            live_id,
            viewer_count,
        )

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

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Track Leave Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to track leave.",
            code="INTERNAL_ERROR",
        )
