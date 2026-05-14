"""
Live Stream APIs (implementation).

Handles:
- start_live
- join_live
- end_live
- get_live
- list_live_streams

Social Live rules:
- Live is hosted by AOS Live Stream.host_user
- Guests can watch using session_id
- Logged-in users can fully interact
- Follow/relationship state is user-to-user
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login, current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from aos.services.livekit_service import LiveKitService
from aos.services.notification_service import NotificationService

from .constants import (
    START_LIVE_LIMIT_PER_MINUTE_PER_USER,
    JOIN_LIVE_LIMIT_PER_MINUTE_PER_USER,
    END_LIVE_LIMIT_PER_MINUTE_PER_USER,
    GET_LIVE_LIMIT_PER_MINUTE_PER_IP,
    LIST_LIVE_STREAMS_LIMIT_PER_MINUTE_PER_IP,
)

from .validators import (
    validate_live_exists,
    validate_live_active,
    validate_user_is_host,
    validate_user_can_go_live,
)

from .realtime import (
    publish_live_started,
    publish_live_ended,
)

from .serializers import (
    get_user_display,
    is_guest_user,
    serialize_live,
    serialize_live_list,
)


LIVE_STATUS = "live"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


# VIEWER / IDENTITY HELPERS
def _viewer_user() -> str | None:
    user = current_user()
    return None if is_guest_user(user) else user


def _normalize_session_id(value) -> str | None:
    session_id = (value or "").strip()
    return session_id or None


def _get_guest_identity(session_id: str) -> str:
    return f"guest:{session_id}"


def _get_livekit_identity(*, viewer: str | None, session_id: str | None) -> str:
    if viewer:
        return viewer

    if session_id:
        return _get_guest_identity(session_id)

    return f"guest:{request_ip()}"


# DATA HELPERS
def _get_followers(user: str) -> list[str]:
    if not user:
        return []

    return frappe.get_all(
        "AOS Follow",
        filters={"following_user": user},
        pluck="follower_user",
    ) or []


def _get_active_live_for_host(host_user: str):
    return frappe.db.get_value(
        "AOS Live Stream",
        {
            "host_user": host_user,
            "status": LIVE_STATUS,
            "is_active": 1,
        },
        [
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
        ],
        as_dict=True,
    )


def _build_livekit_payload(
    *,
    live,
    viewer: str | None,
    session_id: str | None,
    role: str,
) -> dict:
    identity = _get_livekit_identity(
        viewer=viewer,
        session_id=session_id,
    )

    display = get_user_display(viewer)
    guest = is_guest_user(viewer)

    metadata = LiveKitService.build_metadata(
        user=viewer or identity,
        role=role,
        display_name=display.get("display_name"),
        avatar=display.get("avatar"),
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
        "is_guest": guest,
    }


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

    title = (kwargs.get("title") or "").strip()
    cover_image = kwargs.get("cover_image")

    if not title:
        return fail("title is required.", code="VALIDATION_ERROR")

    _, err = validate_user_can_go_live(user)
    if err:
        return err

    try:
        existing_live = _get_active_live_for_host(user)

        if existing_live:
            live_doc = frappe.get_doc("AOS Live Stream", existing_live.name)

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
                        role="host",
                    ),
                },
            )

        live = frappe.new_doc("AOS Live Stream")
        live.host_user = user
        live.title = title
        live.cover_image = cover_image
        live.status = LIVE_STATUS
        live.insert(ignore_permissions=True)

        # after_insert sets room_name using db_set, so reload before token generation.
        live.reload()

        publish_live_started(live)

        followers = _get_followers(user)

        for follower in followers:
            if not follower or follower == user:
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
                    role="host",
                ),
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Start Live Failed")
        frappe.db.rollback()
        return fail("Failed to start live.", code="INTERNAL_ERROR")


# JOIN LIVE / WATCH LIVE
def join_live_impl(**kwargs):
    viewer = _viewer_user()
    session_id = _normalize_session_id(kwargs.get("session_id"))

    if not viewer and not session_id:
        return fail("session_id is required for guest viewers.", code="VALIDATION_ERROR")

    rl_identity = viewer or session_id or request_ip()

    rl = rate_limit(
        key=f"aos:live:join:{rl_identity}",
        ttl_seconds=60,
        limit=JOIN_LIVE_LIMIT_PER_MINUTE_PER_USER,
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

        role = "host" if viewer and live.host_user == viewer else "viewer"

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

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Join Live Failed")
        frappe.db.rollback()
        return fail("Failed to join live.", code="INTERNAL_ERROR")


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

    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_user_is_host(live, user)
        if err:
            return err

        if live.status == "ended":
            return ok(
                "Live already ended.",
                data={
                    "live": serialize_live(
                        live,
                        viewer=user,
                    )
                },
            )

        live.status = "ended"
        live.save(ignore_permissions=True)
        live.reload()

        publish_live_ended(live)

        return ok(
            "Live ended.",
            data={
                "live": serialize_live(
                    live,
                    viewer=user,
                )
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "End Live Failed")
        frappe.db.rollback()
        return fail("Failed to end live.", code="INTERNAL_ERROR")


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

    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    viewer = _viewer_user()
    session_id = _normalize_session_id(kwargs.get("session_id"))

    try:
        live = frappe.db.get_value(
            "AOS Live Stream",
            live_id,
            _live_fields(),
            as_dict=True,
        )

        if not live:
            return fail("Live not found.", code="NOT_FOUND")

        return ok(
            "Live fetched.",
            data={
                "live": serialize_live(
                    live,
                    viewer=viewer,
                    session_id=session_id,
                )
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Get Live Failed")
        return fail("Failed to fetch live.", code="INTERNAL_ERROR")


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
    session_id = _normalize_session_id(kwargs.get("session_id"))

    try:
        limit = int(kwargs.get("limit") or DEFAULT_PAGE_SIZE)
        limit = max(1, min(limit, MAX_PAGE_SIZE))

        start = int(kwargs.get("start") or 0)
        start = max(0, start)

        lives = frappe.get_all(
            "AOS Live Stream",
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
                    "has_more": len(lives) == limit,
                },
            },
        )

    except ValueError:
        return fail("Invalid pagination values.", code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "List Live Failed")
        return fail("Failed to fetch live streams.", code="INTERNAL_ERROR")
