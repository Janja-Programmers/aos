"""
Live Tracking APIs (implementation).

Handles:
- track_join
- track_leave
- Viewer count synchronization
- Host-only viewer-joined system messages

Rules:
- Guests and authenticated viewers require session_id.
- The host does not use viewer tracking.
- The host is never included in viewer metrics.
- user is optional and only stored for authenticated viewers.
- Viewer count is derived from AOS Live Stream View.
- A viewer-joined system message is created only for a new view session.
- Repeated track_join calls for the same active session are idempotent.
- Viewer identities and session IDs are not broadcast to the live room.
- Host-only system messages are not returned to viewers.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shared.validators import require_id
from aos.services.live_analytics_service import LiveAnalyticsService

from .activity import record_live_join_activity
from .constants import (
    TRACK_JOIN_LIMIT_PER_MINUTE_PER_IP,
    TRACK_LEAVE_LIMIT_PER_MINUTE_PER_IP,
)
from .messages import create_live_system_message
from .realtime import (
    publish_live_message_to_user,
    publish_viewer_count,
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


def _normalize_session_id(
    value,
) -> str | None:
    session_id = str(
        value or ""
    ).strip()

    return session_id or None


def _is_live_host(
    *,
    live,
    viewer: str | None,
) -> bool:
    return bool(
        viewer
        and live.host_user == viewer
    )


def _rate_limit_identity(
    *,
    viewer: str | None,
    session_id: str | None,
) -> str:
    return (
        viewer
        or session_id
        or request_ip()
    )


# HOST RESPONSE HELPERS
def _host_tracking_response(
    *,
    live,
    action: str,
):
    """
    Return an idempotent response for accidental host tracking calls.

    The host does not create AOS Live Stream View rows and is therefore not
    included in viewer_count, total_views, peak_viewers, or watch time.
    """
    live.reload()

    if action == "join":
        return ok(
            "Host does not require viewer tracking.",
            data={
                "view_id": None,
                "viewer_count": int(
                    live.viewer_count or 0
                ),
                "is_new_session": False,
                "live": serialize_live(
                    live,
                    viewer=live.host_user,
                    session_id=None,
                ),
            },
        )

    return ok(
        "Host does not use viewer tracking.",
        data={
            "view_id": None,
            "viewer_count": int(
                live.viewer_count or 0
            ),
            "live": serialize_live(
                live,
                viewer=live.host_user,
                session_id=None,
            ),
        },
    )


# VIEW HELPERS
def _get_active_view_by_session(
    *,
    live_id: str,
    session_id: str,
    viewer: str | None,
):
    """
    Return an active view session owned by the current viewer.

    Authenticated sessions are matched by:
    - live stream
    - session ID
    - exact user

    Guest sessions are matched by:
    - live stream
    - session ID
    - empty user
    """
    filters = {
        "live_stream": live_id,
        "session_id": session_id,
        "is_active": 1,
    }

    if viewer:
        filters["user"] = viewer
    else:
        filters["user"] = [
            "is",
            "not set",
        ]

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
    Synchronize derived view counters from AOS Live Stream View.

    LiveAnalyticsService is the canonical owner of Live analytics.
    """

    metrics = LiveAnalyticsService.sync_view_metrics(
        live_id=live_id,
    )

    if metrics:
        return metrics

    viewer_count = frappe.db.count(
        LIVE_VIEW_DOCTYPE,
        filters={
            "live_stream": live_id,
            "is_active": 1,
        },
    )

    return {
        "viewer_count": int(
            viewer_count or 0
        ),
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

    view.insert(
        ignore_permissions=True
    )

    return view


# SYSTEM MESSAGE HELPERS
def _build_join_message_content(
    viewer: str | None,
) -> str:
    if not viewer:
        return GUEST_JOINED_MESSAGE

    display = get_user_display(
        viewer
    )

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
):
    """
    Persist and publish a host-only viewer-joined system message.

    This function is called only after a genuine non-host view session has
    been created.
    """
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
        publish=False,
    )

    publish_live_message_to_user(
        user=live.host_user,
        live_id=live.name,
        message=message,
    )


# TRACK JOIN
def track_join_impl(**kwargs):
    viewer = _viewer_user()

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    rl = rate_limit(
        key=(
            "aos:live:track_join:"
            f"{_rate_limit_identity(viewer=viewer, session_id=session_id)}"
        ),
        ttl_seconds=60,
        limit=TRACK_JOIN_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

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

        # The host owns the broadcast and must never become a viewer row.
        if _is_live_host(
            live=live,
            viewer=viewer,
        ):
            return _host_tracking_response(
                live=live,
                action="join",
            )

        err = validate_view_identity(
            viewer,
            session_id,
        )
        if err:
            return err

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

        record_live_join_activity(
            user=viewer,
            live_id=live_id,
            session_id=session_id,
            view_id=view.name,
        )

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

        _create_viewer_joined_message(
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
                "live": serialize_live(
                    live,
                    viewer=viewer,
                    session_id=session_id,
                ),
            },
        )

    except Exception as ex:
        frappe.db.rollback()

        if is_duplicate_entry_error(ex):
            existing = _get_active_view_by_session(
                live_id=live_id,
                session_id=session_id,
                viewer=viewer,
            )

            if existing:
                metrics = _sync_view_metrics(live_id)
                viewer_count = int(metrics.get("viewer_count") or 0)
                publish_viewer_count(live_id, viewer_count)

                live.reload()

                return ok(
                    "Already joined.",
                    data={
                        "view_id": existing.name,
                        "viewer_count": viewer_count,
                        "is_new_session": False,
                        "live": serialize_live(
                            live,
                            viewer=viewer,
                            session_id=session_id,
                        ),
                    },
                )

        if isinstance(ex, frappe.ValidationError):
            return safe_fail_from_exception(ex, fallback="Invalid request.", code="VALIDATION_ERROR")

        frappe.log_error(
            frappe.get_traceback(),
            "Track Join Failed",
        )

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

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    rl = rate_limit(
        key=(
            "aos:live:track_leave:"
            f"{_rate_limit_identity(viewer=viewer, session_id=session_id)}"
        ),
        ttl_seconds=60,
        limit=TRACK_LEAVE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    try:
        live, err = validate_live_exists(
            live_id
        )
        if err:
            return err

        # Host tracking is bypassed even if the live has just ended.
        if _is_live_host(
            live=live,
            viewer=viewer,
        ):
            return _host_tracking_response(
                live=live,
                action="leave",
            )

        err = validate_view_identity(
            viewer,
            session_id,
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

        view.save(
            ignore_permissions=True
        )

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

        return safe_fail_from_exception(ex, fallback="Invalid request.", code="VALIDATION_ERROR")

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
