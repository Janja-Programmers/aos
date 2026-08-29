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
- Ending a live closes all unresolved and active co-host workflows.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import current_user, require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shared.validators import require_id
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.livekit_service import LiveKitService
from aos.services.notification_service import NotificationService  # noqa: F401
from aos.services.live.livekit import participant_identity, participant_metadata
from aos.services.live.cursor import decode_cursor, encode_cursor
from aos.services.live.errors import LiveError
from aos.services.live.repository import LiveRepository
from aos.services.live.notifications import enqueue_live_started_fanout
from aos.services.live.participants import enqueue_cohost_removal

from .activity import record_live_host_activity
from .constants import (
    END_LIVE_LIMIT_PER_MINUTE_PER_USER,
    GET_LIVE_LIMIT_PER_MINUTE_PER_IP,
    JOIN_LIVE_LIMIT_PER_MINUTE_PER_USER,
    LIST_LIVE_STREAMS_LIMIT_PER_MINUTE_PER_IP,
    START_LIVE_LIMIT_PER_MINUTE_PER_USER,
)
from .messages import (
    create_live_cohost_message,
    create_live_system_message,
)
from .media import (
    attach_live_cover_media,
    looks_like_media_id,
    normalize_media_id,
    validate_live_cover_media_for_use,
)
from .realtime import (
    publish_cohost_cancelled,
    publish_cohost_ended,
    publish_live_ended,
    publish_live_message_to_user,
    publish_live_started,
)
from .serializers import (
    get_user_display,
    is_guest_user,
    serialize_live,
    serialize_live_cohost,
    serialize_live_list,
)
from .validators import (
    validate_live_active,
    validate_live_exists,
    validate_live_social_access,
    validate_user_can_go_live,
    validate_user_is_host,
)


LIVE_STREAM_DOCTYPE = "AOS Live Stream"
LIVE_COHOST_DOCTYPE = "AOS Live CoHost"

LIVE_STATUS = "live"
ENDED_STATUS = "ended"

HOST_ROLE = "host"
VIEWER_ROLE = "viewer"

COHOST_STATUS_PENDING = "pending"
COHOST_STATUS_ACCEPTED = "accepted"
COHOST_STATUS_CANCELLED = "cancelled"
COHOST_STATUS_ACTIVE = "active"
COHOST_STATUS_ENDED = "ended"

COHOST_MESSAGE_ENDED = "cohost_ended"

UNRESOLVED_COHOST_STATUSES = {
    COHOST_STATUS_PENDING,
    COHOST_STATUS_ACCEPTED,
    COHOST_STATUS_ACTIVE,
}

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
    """Return a stable opaque identity without embedding account data."""
    if role != HOST_ROLE and not session_id:
        frappe.throw("Session id is required for viewers.")
    if role == HOST_ROLE and not viewer:
        frappe.throw("Host user is required.")
    return participant_identity(
        live_id=live_id,
        role=role,
        user=viewer,
        session_id=session_id,
    )


# DATA HELPERS
def _live_fields() -> list[str]:
    return [
        "name",
        "creation",
        "title",
        "host_user",
        "status",
        "viewer_count",
        "total_views",
        "unique_viewers",
        "total_joins",
        "peak_viewers",
        "like_count",
        "reaction_count",
        "comment_count",
        "total_watch_time_seconds",
        "cover_image",
        "live_cover_media",
        "room_name",
        "started_at",
        "ended_at",
        "duration_seconds",
        "is_active",
    ]


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

    The host does not use a viewer session and is excluded from viewer count.
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
            error="VALIDATION_ERROR",
        )

    return None


def _workflow_users(
    *,
    host_user: str,
    cohost_user: str,
) -> list[str]:
    return list(
        dict.fromkeys(
            user
            for user in [
                host_user,
                cohost_user,
            ]
            if user
        )
    )


def _enqueue_room_job(method: str, live_id: str) -> None:
    """Schedule external room work only after the application transaction commits."""
    try:
        frappe.enqueue(
            method,
            queue="short",
            enqueue_after_commit=True,
            live_id=live_id,
        )
    except Exception:
        frappe.log_error("Live room job enqueue failed.", "Live room reconciliation enqueue")



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

    metadata = frappe.as_json(
        participant_metadata(
            role=role,
            user=viewer,
            display_name=display.get("display_name"),
            avatar=display.get("avatar"),
            is_guest=guest,
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
        "user": public_account_id_for_user(viewer) if viewer else None,
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
    - Visible to the host and viewers.
    - Published to the live room.

    notifying_followers:
    - Visible only to the host.
    - Published directly to the host's devices.
    """
    host_public_id = public_account_id_for_user(host_user)

    live_started_message = create_live_system_message(
        live_id=live.name,
        message_type="live_started",
        content="Live has started.",
        user=host_user,
        metadata={
            "host_user": host_public_id,
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
            "host_user": host_public_id,
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
    host_public_id = public_account_id_for_user(host_user)

    return create_live_system_message(
        live_id=live.name,
        message_type="live_ended",
        content="Live has ended.",
        user=host_user,
        metadata={
            "host_user": host_public_id,
            "live_id": live.name,
            "duration_seconds": int(
                live.duration_seconds or 0
            ),
        },
        visible_to_host=True,
        visible_to_viewers=True,
        publish=True,
    )


# CO-HOST CLEANUP
def _close_active_cohost(
    *,
    live,
    cohost,
    host_user: str,
    ended_at,
) -> dict:
    """
    End an active co-host before the live stream becomes inactive.
    """
    cohost.status = COHOST_STATUS_ENDED
    cohost.is_active = 0
    cohost.ended_at = ended_at
    cohost.ended_by = host_user
    cohost.end_reason = "live_ended"

    cohost.save(
        ignore_permissions=True
    )
    enqueue_cohost_removal(cohost.name)

    private_payload = serialize_live_cohost(
        cohost,
        include_internal=True,
    )

    public_payload = serialize_live_cohost(
        cohost,
        include_internal=False,
    )

    cohost_display = get_user_display(
        cohost.user
    )

    display_name = (
        cohost_display.get(
            "display_name"
        )
        or "A co-host"
    )

    message = create_live_cohost_message(
        live_id=live.name,
        message_type=COHOST_MESSAGE_ENDED,
        content=(
            f"{display_name} is no longer co-hosting."
        ),
        user=host_user,
        target_user=cohost.user,
        metadata={
            "cohost_id": cohost.name,
            "status": cohost.status,
            "end_reason": "live_ended",
        },
        visible_to_host=True,
        visible_to_viewers=True,
        publish=True,
    )

    publish_cohost_ended(
        live_id=live.name,
        cohost=public_payload,
        private_users=_workflow_users(
            host_user=live.host_user,
            cohost_user=cohost.user,
        ),
        private_cohost=private_payload,
    )

    return {
        "cohost": private_payload,
        "message": message,
    }


def _cancel_unresolved_cohost(
    *,
    live,
    cohost,
    host_user: str,
    cancelled_at,
) -> dict:
    """
    Cancel a pending invitation/request or an accepted workflow that never
    became active.
    """
    previous_status = cohost.status

    cohost.status = COHOST_STATUS_CANCELLED
    cohost.is_active = 0

    # Pending workflows have no responder yet. Record the host as the actor
    # that closed the workflow because the live ended.
    if previous_status == COHOST_STATUS_PENDING:
        cohost.responded_by = host_user
        cohost.responded_at = cancelled_at

    cohost.response_reason = (
        "Live ended before the co-host workflow completed."
    )

    cohost.save(
        ignore_permissions=True
    )

    private_payload = serialize_live_cohost(
        cohost,
        include_internal=True,
    )

    publish_cohost_cancelled(
        users=_workflow_users(
            host_user=live.host_user,
            cohost_user=cohost.user,
        ),
        live_id=live.name,
        cohost=private_payload,
    )

    return private_payload


def _close_cohost_workflows_for_live(
    *,
    live,
    host_user: str,
) -> dict:
    """Close unresolved co-host workflows in bounded deterministic batches."""
    cleanup = {
        "ended": [],
        "cancelled": [],
        "ended_count": 0,
        "cancelled_count": 0,
        "truncated": False,
    }
    response_limit = 100
    closed_at = now_datetime()

    while True:
        rows = frappe.get_all(
            LIVE_COHOST_DOCTYPE,
            filters={
                "live_stream": live.name,
                "status": ["in", list(UNRESOLVED_COHOST_STATUSES)],
            },
            fields=["name"],
            order_by="creation asc, name asc",
            limit_page_length=100,
        )
        if not rows:
            return cleanup

        for row in rows:
            cohost = frappe.get_doc(LIVE_COHOST_DOCTYPE, row.name)

            if cohost.status == COHOST_STATUS_ACTIVE and bool(cohost.is_active):
                result = _close_active_cohost(
                    live=live,
                    cohost=cohost,
                    host_user=host_user,
                    ended_at=closed_at,
                )
                cleanup["ended_count"] += 1
                if len(cleanup["ended"]) < response_limit:
                    cleanup["ended"].append(result)
                else:
                    cleanup["truncated"] = True
                continue

            if cohost.status in {COHOST_STATUS_PENDING, COHOST_STATUS_ACCEPTED}:
                payload = _cancel_unresolved_cohost(
                    live=live,
                    cohost=cohost,
                    host_user=host_user,
                    cancelled_at=closed_at,
                )
                cleanup["cancelled_count"] += 1
                if len(cleanup["cancelled"]) < response_limit:
                    cleanup["cancelled"].append(payload)
                else:
                    cleanup["truncated"] = True


# START LIVE
def start_live_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("live", "start", "user", user),
        ttl_seconds=60,
        limit=START_LIVE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many live start attempts.",
    )
    if rl:
        return rl

    title = str(
        kwargs.get("title") or ""
    ).strip()

    cover_image = str(
        kwargs.get("cover_image") or ""
    ).strip()

    cover_media_id = (
        normalize_media_id(kwargs.get("live_cover_media"))
        or normalize_media_id(kwargs.get("cover_image_media"))
        or normalize_media_id(kwargs.get("media_id"))
    )

    if not cover_media_id and looks_like_media_id(cover_image):
        cover_media_id = normalize_media_id(cover_image)

    if not title:
        return fail(
            "title is required.",
            error="VALIDATION_ERROR",
        )

    _, err = validate_user_can_go_live(
        user
    )
    if err:
        return err

    cover_url = cover_image

    if cover_media_id:
        _media_doc, media_cover_url, err = validate_live_cover_media_for_use(
            media_id=cover_media_id,
            user=user,
        )
        if err:
            return err

        cover_url = media_cover_url or ""

    try:
        # Serialize starts for the same account before checking/creating the
        # DB-enforced active_host_key. The unique field remains the final race
        # boundary across workers.
        frappe.db.sql(
            "SELECT name FROM `tabUser` WHERE name = %s FOR UPDATE",
            (user,),
        )
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
        live.cover_image = cover_url
        if hasattr(live, "live_cover_media"):
            live.live_cover_media = cover_media_id or ""
        live.status = LIVE_STATUS
        live.room_cleanup_pending = 0

        live.insert(
            ignore_permissions=True
        )

        if cover_media_id:
            _attached_media, attached_cover_url, err = attach_live_cover_media(
                media_id=cover_media_id,
                user=user,
                live_id=live.name,
            )
            if err:
                return err

            if attached_cover_url and attached_cover_url != live.cover_image:
                live.cover_image = attached_cover_url
                live.save(ignore_permissions=True)

        live.reload()
        _enqueue_room_job("aos.tasks.live.ensure_live_room", live.name)

        record_live_host_activity(
            user=user,
            live_id=live.name,
        )

        startup_messages = _create_startup_messages(
            live=live,
            host_user=user,
        )

        publish_live_started(
            live
        )

        enqueue_live_started_fanout(live.name)

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

    except Exception as ex:
        if is_duplicate_entry_error(ex):
            existing_live = _get_active_live_for_host(user)
            if existing_live:
                live_doc = frappe.get_doc(LIVE_STREAM_DOCTYPE, existing_live.name)
                return ok(
                    "You already have an active live stream.",
                    data={
                        "live": serialize_live(live_doc, viewer=user),
                        "session": _build_livekit_payload(
                            live=live_doc, viewer=user, session_id=None, role=HOST_ROLE
                        ),
                        "startup_messages": [],
                    },
                )
        if isinstance(ex, frappe.ValidationError):
            return safe_fail_from_exception(
                ex, fallback="Invalid request.", error="VALIDATION_ERROR"
            )
        frappe.log_error(
            "Live start failed.",
            "Start Live Failed",
        )
        return fail(
            "Failed to start live.",
            error="INTERNAL_ERROR",
        )


# JOIN LIVE / WATCH LIVE
def join_live_impl(**kwargs):
    viewer = _viewer_user()

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    rate_limit_identity = (
        viewer
        or session_id
        or request_ip()
    )

    rl = rate_limit(
        key=rate_limit_key("live", "join", rate_limit_identity),
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
        LiveRepository().lock_live_shared(live_id)
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

        err = validate_live_social_access(live=live, user=viewer, lock_relationship=True)
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
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            "Live join failed.",
            "Join Live Failed",
        )
        return fail(
            "Failed to join live.",
            error="INTERNAL_ERROR",
        )


# END LIVE
def end_live_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("live", "end", "user", user),
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
        LiveRepository().lock_live(live_id)
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
                    "cohost_cleanup": {
                        "ended": [],
                        "cancelled": [],
                    },
                },
            )

        # Close co-host workflows while the live is still active.
        cohost_cleanup = (
            _close_cohost_workflows_for_live(
                live=live,
                host_user=user,
            )
        )

        live.status = ENDED_STATUS
        live.room_cleanup_pending = 1

        live.save(
            ignore_permissions=True
        )

        live.reload()
        _enqueue_room_job("aos.tasks.live.cleanup_live_room", live.name)

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
                "cohost_cleanup": cohost_cleanup,
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            "Live end failed.",
            "End Live Failed",
        )
        return fail(
            "Failed to end live.",
            error="INTERNAL_ERROR",
        )


# GET LIVE
def get_live_impl(**kwargs):
    ip = request_ip()

    rl = rate_limit(
        key=rate_limit_key("live", "get", "ip", ip),
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
                error="NOT_FOUND",
            )

        access_err = validate_live_social_access(live=live, user=viewer)
        if access_err:
            return access_err

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
            "Live read failed.",
            "Get Live Failed",
        )

        return fail(
            "Failed to fetch live.",
            error="INTERNAL_ERROR",
        )


# LIST LIVE STREAMS
def list_live_streams_impl(**kwargs):
    ip = request_ip()

    rl = rate_limit(
        key=rate_limit_key("live", "list", "ip", ip),
        ttl_seconds=60,
        limit=LIST_LIVE_STREAMS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    viewer = _viewer_user()
    session_id = _normalize_session_id(kwargs.get("session_id"))

    try:
        limit = max(1, min(int(kwargs.get("limit") or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
        start_offset = max(0, int(kwargs.get("start") or 0))
        cursor_value = str(kwargs.get("cursor") or "").strip()
        if cursor_value and start_offset:
            return fail("cursor and start cannot be combined.", error="VALIDATION_ERROR")

        cursor = decode_cursor(cursor_value) if cursor_value else None
        params: dict[str, object] = {"status": LIVE_STATUS, "limit": limit + 1, "offset": start_offset}
        cursor_sql = ""
        if cursor:
            started_at = str(cursor.get("started_at") or "")
            creation = str(cursor.get("creation") or "")
            name = str(cursor.get("name") or "")
            if cursor.get("kind") != "live_feed" or not started_at or not creation or not name:
                return fail("Invalid Live cursor.", error="LIVE_INVALID_CURSOR")
            params.update({"cursor_started_at": started_at, "cursor_creation": creation, "cursor_name": name})
            cursor_sql = """
              AND (
                    l.started_at < %(cursor_started_at)s
                 OR (l.started_at = %(cursor_started_at)s AND l.creation < %(cursor_creation)s)
                 OR (l.started_at = %(cursor_started_at)s AND l.creation = %(cursor_creation)s AND l.name < %(cursor_name)s)
              )
            """

        block_sql = ""
        if viewer:
            params["viewer"] = viewer
            block_sql = """
              AND NOT EXISTS (
                    SELECT 1 FROM `tabAOS User Block` b
                    WHERE b.status = 'Active'
                      AND ((b.blocker_user = %(viewer)s AND b.blocked_user = l.host_user)
                        OR (b.blocker_user = l.host_user AND b.blocked_user = %(viewer)s))
              )
            """

        rows = frappe.db.sql(
            f"""
            SELECT l.name, l.creation, l.title, l.host_user, l.status, l.viewer_count,
                   l.total_views, l.unique_viewers, l.total_joins, l.peak_viewers,
                   l.like_count, l.reaction_count, l.comment_count, l.total_watch_time_seconds,
                   l.cover_image, l.live_cover_media, l.room_name, l.started_at, l.ended_at,
                   l.duration_seconds, l.is_active
            FROM `tabAOS Live Stream` l
            INNER JOIN `tabUser` u ON u.name = l.host_user AND u.enabled = 1
            INNER JOIN `tabAOS Profile` p ON p.user = l.host_user
            WHERE l.status = %(status)s AND l.is_active = 1
              AND COALESCE(p.is_deleted, 0) = 0
              AND COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'
              {block_sql}
              {cursor_sql}
            ORDER BY l.started_at DESC, l.creation DESC, l.name DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
            as_dict=True,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = encode_cursor(
                {
                    "kind": "live_feed",
                    "started_at": str(last.started_at),
                    "creation": str(last.creation),
                    "name": str(last.name),
                }
            )

        items = serialize_live_list(page, viewer=viewer, session_id=session_id)
        return ok(
            "Live streams fetched.",
            data={
                "items": items,
                "pagination": {
                    "start": start_offset,
                    "limit": limit,
                    "count": len(items),
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                },
            },
        )
    except LiveError as exc:
        return fail(exc.public_message, error=exc.code, data=exc.data, http_status=exc.http_status)
    except ValueError:
        return fail("Invalid pagination values.", error="VALIDATION_ERROR")
    except Exception:
        frappe.log_error("Live feed failed.", "List Live Failed")
        return fail("Failed to fetch live streams.", error="INTERNAL_ERROR")

