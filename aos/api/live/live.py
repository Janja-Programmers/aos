"""
Live Stream APIs (implementation).

Handles:
- start_live
- join_live
- end_live
- get_live
- list_live_streams

Social Live rules:
- Live is hosted by AOS Live Stream.host_user.
- Every non-host viewer requires a session_id.
- Guests use session-scoped LiveKit identities.
- Authenticated viewers use user-and-session-scoped LiveKit identities.
- The host uses a live-scoped host identity.
- Follow and relationship state is user-to-user.
- Lifecycle system messages are stored in AOS Live Message.
- Host-only messages are never broadcast to the live room.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import current_user, require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import require_id
from aos.services.livekit_service import LiveKitService
from aos.services.notification_service import NotificationService

from .constants import (
    END_LIVE_LIMIT_PER_MINUTE_PER_USER,
    GET_LIVE_LIMIT_PER_MINUTE_PER_IP,
    JOIN_LIVE_LIMIT_PER_MINUTE_PER_USER,
    LIST_LIVE_STREAMS_LIMIT_PER_MINUTE_PER_IP,
    START_LIVE_LIMIT_PER_MINUTE_PER_USER,
)
from .messages import create_live_system_message
from .realtime import (
    publish_live_ended,
    publish_live_message_to_user,
    publish_live_started,
)
from .serializers import (
    get_user_display,
    is_guest_user,
    serialize_live,
    serialize_live_list,
)
from .validators import (
    validate_live_active,
    validate_live_exists,
    validate_user_can_go_live,
    validate_user_is_host,
)


LIVE_STREAM_DOCTYPE = "AOS Live Stream"

LIVE_STATUS = "live"
ENDED_STATUS = "ended"

HOST_ROLE = "host"
VIEWER_ROLE = "viewer"

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


# VIEWER / IDENTITY HELPERS
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


def _get_livekit_identity(
    *,
    live_id: str,
    viewer: str | None,
    session_id: str | None,
    role: str,
) -> str:
    """
    Build the canonical LiveKit participant identity.

    Host:
        user:{user}:host:{live_id}

    Authenticated viewer:
        user:{user}:session:{session_id}

    Guest viewer:
        guest:{session_id}

    Viewer identities are session-scoped so the same authenticated user can
    join from multiple devices or browser tabs without LiveKit participant
    identity collisions.

    A future viewer-to-cohost role upgrade should preserve the authenticated
    viewer identity instead of creating a different participant identity.
    """
    if role == HOST_ROLE:
        if not viewer:
            frappe.throw(
                "Host user is required."
            )

        return (
            f"user:{viewer}:"
            f"host:{live_id}"
        )

    if not session_id:
        frappe.throw(
            "Session id is required for viewers."
        )

    if viewer:
        return (
            f"user:{viewer}:"
            f"session:{session_id}"
        )

    return f"guest:{session_id}"


# DATA HELPERS
def _live_fields() -> list[str]:
    return [
        "name",
        "title",
        "host_user",
        "status",
        "viewer_count",
        "total_views",
        "peak_viewers",
        "like_count",
        "reaction_count",
        "comment_count",
        "total_watch_time_seconds",
        "cover_image",
        "room_name",
        "started_at",
        "ended_at",
        "duration_seconds",
        "is_active",
    ]


def _get_followers(
    user: str,
) -> list[str]:
    if not user:
        return []

    return (
        frappe.get_all(
            "AOS Follow",
            filters={
                "following_user": user,
            },
            pluck="follower_user",
        )
        or []
    )


def _get_active_live_for_host(
    host_user: str,
):
    return frappe.db.get_value(
        LIVE_STREAM_DOCTYPE,
        {
            "host_user": host_user,
            "status": LIVE_STATUS,
            "is_active": 1,
        },
        _live_fields(),
        as_dict=True,
    )


def _validate_viewer_session(
    *,
    viewer: str | None,
    host_user: str,
    session_id: str | None,
):
    """
    Require a session ID for every non-host participant.

    The host does not use a normal viewer session and is not included in the
    viewer count.
    """
    is_host = bool(
        viewer
        and viewer == host_user
    )

    if is_host:
        return None

    if not session_id:
        return fail(
            "session_id is required for viewers.",
            code="VALIDATION_ERROR",
        )

    return None


# LIVEKIT HELPERS
def _build_livekit_payload(
    *,
    live,
    viewer: str | None,
    session_id: str | None,
    role: str,
) -> dict:
    identity = _get_livekit_identity(
        live_id=live.name,
        viewer=viewer,
        session_id=session_id,
        role=role,
    )

    display = get_user_display(
        viewer
    )

    guest = is_guest_user(
        viewer
    )

    metadata = LiveKitService.build_metadata(
        # Store the stable AOS user separately from the participant identity.
        user=viewer or identity,
        role=role,
        display_name=display.get(
            "display_name"
        ),
        avatar=display.get(
            "avatar"
        ),
        is_guest=guest,
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
        "user": viewer,
        "is_guest": guest,
        "session_id": session_id,
    }


# LIVE SYSTEM MESSAGE HELPERS
def _create_startup_messages(
    *,
    live,
    host_user: str,
) -> list[dict]:
    """
    Create initial lifecycle messages.

    live_started:
    - Stored in the feed.
    - Visible to the host and viewers.
    - Published to the live room.

    notifying_followers:
    - Stored in the feed.
    - Visible only to the host.
    - Published directly to the host's devices.
    """
    live_started_message = create_live_system_message(
        live_id=live.name,
        message_type="live_started",
        content="Live has started.",
        user=host_user,
        metadata={
            "host_user": host_user,
            "live_id": live.name,
        },
        visible_to_host=True,
        visible_to_viewers=True,
        publish=True,
    )

    notifying_message = create_live_system_message(
        live_id=live.name,
        message_type="notifying_followers",
        content="We are notifying people to join.",
        user=host_user,
        metadata={
            "host_user": host_user,
            "live_id": live.name,
        },
        visible_to_host=True,
        visible_to_viewers=False,
        publish=False,
    )

    publish_live_message_to_user(
        user=host_user,
        live_id=live.name,
        message=notifying_message,
    )

    return [
        live_started_message,
        notifying_message,
    ]


def _create_live_ended_message(
    *,
    live,
    host_user: str,
) -> dict:
    """
    Create the public live-ended lifecycle message.
    """
    return create_live_system_message(
        live_id=live.name,
        message_type="live_ended",
        content="Live has ended.",
        user=host_user,
        metadata={
            "host_user": host_user,
            "live_id": live.name,
            "duration_seconds": int(
                live.duration_seconds or 0
            ),
        },
        visible_to_host=True,
        visible_to_viewers=True,
        publish=True,
    )


# START LIVE
def start_live_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:start:user:{user}",
        ttl_seconds=60,
        limit=START_LIVE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many live start attempts.",
    )
    if rl:
        return rl

    title = str(
        kwargs.get("title") or ""
    ).strip()

    cover_image = kwargs.get(
        "cover_image"
    )

    if not title:
        return fail(
            "title is required.",
            code="VALIDATION_ERROR",
        )

    _, err = validate_user_can_go_live(
        user
    )
    if err:
        return err

    try:
        existing_live = _get_active_live_for_host(
            user
        )

        if existing_live:
            live_doc = frappe.get_doc(
                LIVE_STREAM_DOCTYPE,
                existing_live.name,
            )

            return ok(
                "You already have an active live stream.",
                data={
                    "live": serialize_live(
                        live_doc,
                        viewer=user,
                    ),
                    "session": _build_livekit_payload(
                        live=live_doc,
                        viewer=user,
                        session_id=None,
                        role=HOST_ROLE,
                    ),
                    "startup_messages": [],
                },
            )

        live = frappe.new_doc(
            LIVE_STREAM_DOCTYPE
        )

        live.host_user = user
        live.title = title
        live.cover_image = cover_image
        live.status = LIVE_STATUS

        live.insert(
            ignore_permissions=True
        )

        # The Live Stream controller creates room_name and lifecycle fields.
        live.reload()

        startup_messages = _create_startup_messages(
            live=live,
            host_user=user,
        )

        publish_live_started(
            live
        )

        for follower in _get_followers(
            user
        ):
            if (
                not follower
                or follower == user
            ):
                continue

            NotificationService.notify_live_started(
                user=follower,
                host_user=user,
                live_id=live.name,
                title=live.title,
            )

        return ok(
            "Live started.",
            data={
                "live": serialize_live(
                    live,
                    viewer=user,
                ),
                "session": _build_livekit_payload(
                    live=live,
                    viewer=user,
                    session_id=None,
                    role=HOST_ROLE,
                ),
                "startup_messages": startup_messages,
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
            "Start Live Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to start live.",
            code="INTERNAL_ERROR",
        )


# JOIN LIVE / WATCH LIVE
def join_live_impl(**kwargs):
    viewer = _viewer_user()

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    rl_identity = (
        viewer
        or session_id
        or request_ip()
    )

    rl = rate_limit(
        key=f"aos:live:join:{rl_identity}",
        ttl_seconds=60,
        limit=JOIN_LIVE_LIMIT_PER_MINUTE_PER_USER,
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

        err = validate_live_active(
            live
        )
        if err:
            return err

        err = _validate_viewer_session(
            viewer=viewer,
            host_user=live.host_user,
            session_id=session_id,
        )
        if err:
            return err

        is_host = bool(
            viewer
            and live.host_user == viewer
        )

        role = (
            HOST_ROLE
            if is_host
            else VIEWER_ROLE
        )

        return ok(
            "Joined live.",
            data={
                "live": serialize_live(
                    live,
                    viewer=viewer,
                    session_id=session_id,
                ),
                "session": _build_livekit_payload(
                    live=live,
                    viewer=viewer,
                    session_id=session_id,
                    role=role,
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
            "Join Live Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to join live.",
            code="INTERNAL_ERROR",
        )


# END LIVE
def end_live_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:end:user:{user}",
        ttl_seconds=60,
        limit=END_LIVE_LIMIT_PER_MINUTE_PER_USER,
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

        err = validate_user_is_host(
            live,
            user,
        )
        if err:
            return err

        if live.status == ENDED_STATUS:
            return ok(
                "Live already ended.",
                data={
                    "live": serialize_live(
                        live,
                        viewer=user,
                    ),
                    "message": None,
                },
            )

        live.status = ENDED_STATUS

        live.save(
            ignore_permissions=True
        )

        live.reload()

        ended_message = _create_live_ended_message(
            live=live,
            host_user=user,
        )

        publish_live_ended(
            live
        )

        return ok(
            "Live ended.",
            data={
                "live": serialize_live(
                    live,
                    viewer=user,
                ),
                "message": ended_message,
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
            "End Live Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to end live.",
            code="INTERNAL_ERROR",
        )


# GET LIVE
def get_live_impl(**kwargs):
    ip = request_ip()

    rl = rate_limit(
        key=f"aos:live:get:ip:{ip}",
        ttl_seconds=60,
        limit=GET_LIVE_LIMIT_PER_MINUTE_PER_IP,
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

    viewer = _viewer_user()

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    try:
        live = frappe.db.get_value(
            LIVE_STREAM_DOCTYPE,
            live_id,
            _live_fields(),
            as_dict=True,
        )

        if not live:
            return fail(
                "Live not found.",
                code="NOT_FOUND",
            )

        return ok(
            "Live fetched.",
            data={
                "live": serialize_live(
                    live,
                    viewer=viewer,
                    session_id=session_id,
                ),
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Get Live Failed",
        )

        return fail(
            "Failed to fetch live.",
            code="INTERNAL_ERROR",
        )


# LIST LIVE STREAMS
def list_live_streams_impl(**kwargs):
    ip = request_ip()

    rl = rate_limit(
        key=f"aos:live:list:ip:{ip}",
        ttl_seconds=60,
        limit=LIST_LIVE_STREAMS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    viewer = _viewer_user()

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    try:
        limit = int(
            kwargs.get("limit")
            or DEFAULT_PAGE_SIZE
        )

        limit = max(
            1,
            min(
                limit,
                MAX_PAGE_SIZE,
            ),
        )

        start = int(
            kwargs.get("start")
            or 0
        )

        start = max(
            0,
            start,
        )

        lives = frappe.get_all(
            LIVE_STREAM_DOCTYPE,
            filters={
                "status": LIVE_STATUS,
                "is_active": 1,
            },
            fields=_live_fields(),
            order_by="creation desc",
            limit_start=start,
            limit_page_length=limit,
        )

        return ok(
            "Live streams fetched.",
            data={
                "items": serialize_live_list(
                    lives,
                    viewer=viewer,
                    session_id=session_id,
                ),
                "pagination": {
                    "start": start,
                    "limit": limit,
                    "count": len(lives),
                    "has_more": (
                        len(lives)
                        == limit
                    ),
                },
            },
        )

    except ValueError:
        return fail(
            "Invalid pagination values.",
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "List Live Failed",
        )

        return fail(
            "Failed to fetch live streams.",
            code="INTERNAL_ERROR",
        )
