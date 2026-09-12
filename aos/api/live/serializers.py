"""
Live serializers.

Responsibilities:
- Keep stable backend IDs internally.
- Return display-ready API and realtime payloads externally.
- Compute viewer-specific state for Live responses.
- Serialize unified live messages.
- Serialize co-host requests and active co-host sessions.
- Avoid storing duplicated display data on Live DocTypes.

Canonical ownership:
- AOS Live Stream.host_user = live host / creator user

Live message model:
- AOS Live Message supports comments, replies, system events,
  co-host events, gifts, and moderation events.

Co-host model:
- AOS Live CoHost represents host invitations, viewer requests,
  accepted requests, active sessions, and completed sessions.

Guest policy:
- Guests can watch live streams.
- Guests can read viewer-visible live messages.
- Guests cannot comment, react, report, follow, request co-hosting,
  or end lives.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.formatters import humanize_count
from aos.api.shared.user_display import get_user_display as shared_get_user_display
from aos.api.shared.user_display import get_user_display_map
from aos.services.social.capabilities import SocialCapabilityService
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.social.repository import SocialRepository
from aos.services.social.serializers import relationship_map as social_relationship_map


LIVE_STATUS = "live"

LIVE_VIEW_DOCTYPE = "AOS Live Stream View"
LIVE_MESSAGE_DOCTYPE = "AOS Live Message"
LIVE_COHOST_DOCTYPE = "AOS Live CoHost"

LIVE_MESSAGE_KIND_COMMENT = "comment"
LIVE_MESSAGE_KIND_SYSTEM = "system"
LIVE_MESSAGE_KIND_COHOST = "cohost"
LIVE_MESSAGE_KIND_GIFT = "gift"
LIVE_MESSAGE_KIND_MODERATION = "moderation"

LIVE_MESSAGE_TYPE_COMMENT = "comment"
LIVE_MESSAGE_TYPE_REPLY = "reply"

COHOST_REQUEST_TYPE_HOST_INVITE = "host_invite"
COHOST_REQUEST_TYPE_VIEWER_REQUEST = "viewer_request"

COHOST_STATUS_PENDING = "pending"
COHOST_STATUS_ACCEPTED = "accepted"
COHOST_STATUS_REJECTED = "rejected"
COHOST_STATUS_CANCELLED = "cancelled"
COHOST_STATUS_ACTIVE = "active"
COHOST_STATUS_ENDED = "ended"
COHOST_STATUS_EXPIRED = "expired"

COHOST_TERMINAL_STATUSES = {
    COHOST_STATUS_REJECTED,
    COHOST_STATUS_CANCELLED,
    COHOST_STATUS_ENDED,
    COHOST_STATUS_EXPIRED,
}

COHOST_UNRESOLVED_STATUSES = {
    COHOST_STATUS_PENDING,
    COHOST_STATUS_ACCEPTED,
    COHOST_STATUS_ACTIVE,
}


# GENERIC HELPERS
def _value(
    row,
    fieldname: str,
    default=None,
):
    """
    Read a value from:
    - a Frappe Document
    - a frappe._dict
    - a regular dictionary
    """

    if isinstance(row, dict):
        return row.get(
            fieldname,
            default,
        )

    return getattr(
        row,
        fieldname,
        default,
    )


def _as_int(
    value,
    default: int = 0,
) -> int:
    try:
        return int(
            value or default
        )
    except (TypeError, ValueError):
        return default


def _as_bool(
    value,
) -> bool:
    return bool(value)


def _parse_json_object(
    value: Any,
) -> dict:
    """
    Parse a value into a JSON object.

    Invalid or missing data safely becomes an empty dictionary.
    """

    if value in (
        None,
        "",
        {},
    ):
        return {}

    if isinstance(value, dict):
        return dict(value)

    try:
        parsed = frappe.parse_json(
            value
        )
    except Exception:
        return {}

    if not isinstance(parsed, dict):
        return {}

    return parsed


def is_guest_user(
    user: str | None,
) -> bool:
    return (
        not user
        or user == "Guest"
    )


def _apply_viewer_ownership_filter(
    *,
    filters: dict,
    viewer: str | None,
):
    """
    Secure a view-session query to the expected owner.

    Authenticated viewer:
    - user must match exactly.

    Guest viewer:
    - user must be empty.
    """
    if (
        viewer
        and not is_guest_user(viewer)
    ):
        filters["user"] = viewer
    else:
        filters["user"] = [
            "is",
            "not set",
        ]


# USER DISPLAY
def get_user_display(
    user: str | None,
) -> dict:
    """Return display-safe User context."""

    return shared_get_user_display(user)


def get_profile_context(
    user: str | None,
) -> dict:
    """
    Return profile and social context for a user.

    A missing AOS Profile must never break Live serialization.
    """
    if not user:
        return {
            "is_verified": False,
            "total_followers": 0,
        }

    profile = frappe.db.get_value(
        "AOS Profile",
        {
            "user": user,
        },
        [
            "is_verified",
            "total_followers",
        ],
        as_dict=True,
    )

    if not profile:
        return {
            "is_verified": False,
            "total_followers": 0,
        }

    return {
        "is_verified": bool(
            profile.is_verified
        ),
        "total_followers": int(
            profile.total_followers
            or 0
        ),
    }


def serialize_user(
    user: str | None,
) -> dict:
    display = get_user_display(
        user
    )

    profile = get_profile_context(
        user
    )

    is_deleted = bool(
        display.get("is_deleted")
    )

    return {
        "user": display.get("account_id"),
        "display_name": display[
            "display_name"
        ],
        "avatar": display["avatar"],
        "is_deleted": is_deleted,
        "is_live": bool(display.get("is_live")) if not is_deleted else False,
        "live_id": display.get("live_id") if not is_deleted else None,
        "live_status": display.get("live_status") if not is_deleted else None,
        "live_title": display.get("live_title") if not is_deleted else None,
        "live_cover_image": display.get("live_cover_image") if not is_deleted else None,
        "live_started_at": display.get("live_started_at") if not is_deleted else None,
        "live_viewer_count": int(display.get("live_viewer_count") or 0) if not is_deleted else 0,
        "live_viewer_count_display": humanize_count(
            int(display.get("live_viewer_count") or 0)
            if not is_deleted
            else 0
        ),
        "is_verified": (
            False
            if is_deleted
            else profile["is_verified"]
        ),
        "total_followers": (
            0
            if is_deleted
            else profile["total_followers"]
        ),
        "total_followers_display": humanize_count(
            0
            if is_deleted
            else profile["total_followers"]
        ),
    }


def empty_user_payload() -> dict:
    return {
        "user": None,
        "display_name": None,
        "avatar": None,
        "is_deleted": False,
        "is_live": False,
        "live_id": None,
        "live_status": None,
        "live_title": None,
        "live_cover_image": None,
        "live_started_at": None,
        "live_viewer_count": 0,
        "live_viewer_count_display": "0",
        "is_verified": False,
        "total_followers": 0,
        "total_followers_display": "0",
    }


def fallback_user_payload(
    user: str | None,
) -> dict:
    if not user:
        return empty_user_payload()

    try:
        public_id = public_account_id_for_user(user)
    except Exception:
        public_id = None

    return {
        "user": public_id,
        "display_name": "AOS User",
        "avatar": None,
        "is_deleted": False,
        "is_live": False,
        "live_id": None,
        "live_status": None,
        "live_title": None,
        "live_cover_image": None,
        "live_started_at": None,
        "live_viewer_count": 0,
        "live_viewer_count_display": "0",
        "is_verified": False,
        "total_followers": 0,
        "total_followers_display": "0",
    }


# LIVE MESSAGE SERIALIZATION
def live_message_fields() -> list[str]:
    """
    Canonical fields used when querying AOS Live Message.
    """
    return [
        "name",
        "live_stream",
        "message_kind",
        "message_type",
        "user",
        "target_user",
        "content",
        "metadata_json",
        "status",
        "parent_message",
        "root_message",
        "reply_count",
        "visible_to_host",
        "visible_to_viewers",
        "creation",
        "modified",
    ]


def serialize_live_message(
    message,
    *,
    preloaded_user: dict | None = None,
    preloaded_target_user: dict | None = None,
) -> dict:
    """
    Serialize one AOS Live Message document or query row.

    This payload is shared by:
    - add message responses
    - reply responses
    - message and reply lists
    - realtime aos_live_message events
    """
    user_id = _value(
        message,
        "user",
    )

    target_user_id = _value(
        message,
        "target_user",
    )

    if user_id:
        actor = (
            preloaded_user
            or serialize_user(user_id)
            or fallback_user_payload(user_id)
        )
    else:
        actor = empty_user_payload()

    if target_user_id:
        target = (
            preloaded_target_user
            or serialize_user(target_user_id)
            or fallback_user_payload(
                target_user_id
            )
        )
    else:
        target = None

    message_kind = _value(
        message,
        "message_kind",
    )

    message_type = _value(
        message,
        "message_type",
    )
    actor_deleted = bool(actor.get("is_deleted"))
    redact_authored_content = actor_deleted and message_kind == LIVE_MESSAGE_KIND_COMMENT

    return {
        "id": _value(
            message,
            "name",
        ),
        "message_id": _value(
            message,
            "name",
        ),
        "live_stream": _value(
            message,
            "live_stream",
        ),

        # Message classification.
        "kind": message_kind,
        "message_kind": message_kind,
        "message_type": message_type,

        # Actor identity.
        "user": actor["user"],
        "display_name": actor[
            "display_name"
        ],
        "avatar": actor["avatar"],
        "is_verified": bool(
            actor["is_verified"]
        ),
        "total_followers": _as_int(
            actor.get(
                "total_followers"
            )
        ),
        "total_followers_display": actor.get(
            "total_followers_display"
        ) or humanize_count(
            actor.get(
                "total_followers"
            )
        ),
        "actor": (
            actor
            if user_id
            else None
        ),

        # Optional target identity.
        "target_user": target.get("user") if target else None,
        "target": target,

        # Message content.
        "content": None if redact_authored_content else _value(
            message,
            "content",
        ),
        "metadata": {} if redact_authored_content else _parse_json_object(
            _value(
                message,
                "metadata_json",
            )
        ),

        # Message state.
        "status": _value(
            message,
            "status",
        ),
        "parent_message": _value(
            message,
            "parent_message",
        ),
        "root_message": _value(
            message,
            "root_message",
        ),
        "reply_count": _as_int(
            _value(
                message,
                "reply_count",
            )
        ),
        "reply_count_display": humanize_count(
            _as_int(
                _value(
                    message,
                    "reply_count",
                )
            )
        ),

        # Visibility.
        "visible_to_host": _as_bool(
            _value(
                message,
                "visible_to_host",
            )
        ),
        "visible_to_viewers": _as_bool(
            _value(
                message,
                "visible_to_viewers",
            )
        ),

        # Derived client flags.
        "is_comment": (
            message_kind
            == LIVE_MESSAGE_KIND_COMMENT
        ),
        "is_reply": (
            message_kind
            == LIVE_MESSAGE_KIND_COMMENT
            and message_type
            == LIVE_MESSAGE_TYPE_REPLY
        ),
        "is_system": (
            message_kind
            == LIVE_MESSAGE_KIND_SYSTEM
        ),
        "is_cohost_event": (
            message_kind
            == LIVE_MESSAGE_KIND_COHOST
        ),
        "is_gift": (
            message_kind
            == LIVE_MESSAGE_KIND_GIFT
        ),
        "is_moderation": (
            message_kind
            == LIVE_MESSAGE_KIND_MODERATION
        ),

        # Timestamps.
        "creation": _value(
            message,
            "creation",
        ),
        "modified": _value(
            message,
            "modified",
        ),
    }


def serialize_live_messages(
    messages: list,
) -> list[dict]:
    """
    Batch serialize Live Message rows without per-message user queries.
    """
    if not messages:
        return []

    users_to_preload = {
        _value(
            message,
            "user",
        )
        for message in messages
        if _value(
            message,
            "user",
        )
    }

    users_to_preload.update(
        {
            _value(
                message,
                "target_user",
            )
            for message in messages
            if _value(
                message,
                "target_user",
            )
        }
    )

    users = preload_users(
        list(users_to_preload)
    )

    items: list[dict] = []

    for message in messages:
        user_id = _value(
            message,
            "user",
        )

        target_user_id = _value(
            message,
            "target_user",
        )

        items.append(
            serialize_live_message(
                message,
                preloaded_user=(
                    users.get(user_id)
                    if user_id
                    else None
                ),
                preloaded_target_user=(
                    users.get(
                        target_user_id
                    )
                    if target_user_id
                    else None
                ),
            )
        )

    return items


# LIVE CO-HOST SERIALIZATION
def live_cohost_fields() -> list[str]:
    """
    Canonical fields used when querying AOS Live CoHost.

    Co-host APIs should use this list to keep their payloads consistent.
    """
    return [
        "name",
        "live_stream",
        "user",
        "session_id",
        "request_type",
        "status",
        "is_active",
        "requested_by",
        "requested_at",
        "expires_at",
        "responded_by",
        "responded_at",
        "accepted_at",
        "livekit_identity",
        "started_at",
        "ended_at",
        "ended_by",
        "end_reason",
        "response_reason",
        "metadata_json",
        "creation",
        "modified",
    ]


def serialize_live_cohost(
    cohost,
    *,
    preloaded_user: dict | None = None,
    preloaded_requested_by: dict | None = None,
    preloaded_responded_by: dict | None = None,
    preloaded_ended_by: dict | None = None,
    include_internal: bool = False,
) -> dict:
    """
    Serialize one AOS Live CoHost document or query row.

    By default, internal participant identifiers are not exposed:

    - session_id
    - livekit_identity
    - metadata_json

    Set include_internal=True only for trusted, targeted responses such as:
    - the live host
    - the co-host candidate
    - co-host token generation
    - targeted realtime workflow events
    """
    user_id = _value(
        cohost,
        "user",
    )

    requested_by_id = _value(
        cohost,
        "requested_by",
    )

    responded_by_id = _value(
        cohost,
        "responded_by",
    )

    ended_by_id = _value(
        cohost,
        "ended_by",
    )

    candidate = (
        preloaded_user
        or serialize_user(user_id)
        or fallback_user_payload(user_id)
    )

    requester = (
        preloaded_requested_by
        or (
            serialize_user(
                requested_by_id
            )
            if requested_by_id
            else None
        )
    )

    responder = (
        preloaded_responded_by
        or (
            serialize_user(
                responded_by_id
            )
            if responded_by_id
            else None
        )
    )

    ended_by = (
        preloaded_ended_by
        or (
            serialize_user(
                ended_by_id
            )
            if ended_by_id
            else None
        )
    )

    request_type = _value(
        cohost,
        "request_type",
    )

    status = _value(
        cohost,
        "status",
    )

    payload = {
        "id": _value(
            cohost,
            "name",
        ),
        "cohost_id": _value(
            cohost,
            "name",
        ),
        "live_id": _value(
            cohost,
            "live_stream",
        ),
        "live_stream": _value(
            cohost,
            "live_stream",
        ),

        # Candidate.
        "user": candidate["user"],
        "display_name": candidate[
            "display_name"
        ],
        "avatar": candidate["avatar"],
        "is_verified": bool(
            candidate["is_verified"]
        ),
        "total_followers": _as_int(
            candidate.get(
                "total_followers"
            )
        ),
        "total_followers_display": candidate.get(
            "total_followers_display"
        ) or humanize_count(
            candidate.get(
                "total_followers"
            )
        ),
        "cohost": candidate,

        # Workflow classification and state.
        "request_type": request_type,
        "status": status,
        "is_active": _as_bool(
            _value(
                cohost,
                "is_active",
            )
        ),

        # Request details.
        "requested_by": requester.get("user") if requester else None,
        "requester": requester,
        "requested_at": _value(
            cohost,
            "requested_at",
        ),
        "expires_at": _value(
            cohost,
            "expires_at",
        ),

        # Response details.
        "responded_by": responder.get("user") if responder else None,
        "responder": responder,
        "responded_at": _value(
            cohost,
            "responded_at",
        ),
        "accepted_at": _value(
            cohost,
            "accepted_at",
        ),
        "response_reason": _value(
            cohost,
            "response_reason",
        ),

        # Active-session lifecycle.
        "started_at": _value(
            cohost,
            "started_at",
        ),
        "ended_at": _value(
            cohost,
            "ended_at",
        ),
        "ended_by": ended_by.get("user") if ended_by else None,
        "ended_by_user": ended_by,
        "end_reason": _value(
            cohost,
            "end_reason",
        ),

        # Derived workflow flags.
        "is_host_invite": (
            request_type
            == COHOST_REQUEST_TYPE_HOST_INVITE
        ),
        "is_viewer_request": (
            request_type
            == COHOST_REQUEST_TYPE_VIEWER_REQUEST
        ),
        "is_pending": (
            status
            == COHOST_STATUS_PENDING
        ),
        "is_accepted": (
            status
            == COHOST_STATUS_ACCEPTED
        ),
        "is_rejected": (
            status
            == COHOST_STATUS_REJECTED
        ),
        "is_cancelled": (
            status
            == COHOST_STATUS_CANCELLED
        ),
        "is_ended": (
            status
            == COHOST_STATUS_ENDED
        ),
        "is_expired": (
            status
            == COHOST_STATUS_EXPIRED
        ),
        "is_terminal": (
            status
            in COHOST_TERMINAL_STATUSES
        ),

        # Audit timestamps.
        "creation": _value(
            cohost,
            "creation",
        ),
        "modified": _value(
            cohost,
            "modified",
        ),
    }

    if include_internal:
        payload.update(
            {
                "session_id": _value(
                    cohost,
                    "session_id",
                ),
                "livekit_identity": _value(
                    cohost,
                    "livekit_identity",
                ),
                "metadata": _parse_json_object(
                    _value(
                        cohost,
                        "metadata_json",
                    )
                ),
            }
        )

    return payload


def serialize_live_cohosts(
    cohosts: list,
    *,
    include_internal: bool = False,
) -> list[dict]:
    """
    Batch serialize co-host rows without per-record user queries.
    """
    if not cohosts:
        return []

    users_to_preload: set[str] = set()

    for cohost in cohosts:
        for fieldname in (
            "user",
            "requested_by",
            "responded_by",
            "ended_by",
        ):
            user_id = _value(
                cohost,
                fieldname,
            )

            if user_id:
                users_to_preload.add(
                    user_id
                )

    users = preload_users(
        list(users_to_preload)
    )

    items: list[dict] = []

    for cohost in cohosts:
        user_id = _value(
            cohost,
            "user",
        )

        requested_by_id = _value(
            cohost,
            "requested_by",
        )

        responded_by_id = _value(
            cohost,
            "responded_by",
        )

        ended_by_id = _value(
            cohost,
            "ended_by",
        )

        items.append(
            serialize_live_cohost(
                cohost,
                preloaded_user=(
                    users.get(user_id)
                    if user_id
                    else None
                ),
                preloaded_requested_by=(
                    users.get(
                        requested_by_id
                    )
                    if requested_by_id
                    else None
                ),
                preloaded_responded_by=(
                    users.get(
                        responded_by_id
                    )
                    if responded_by_id
                    else None
                ),
                preloaded_ended_by=(
                    users.get(
                        ended_by_id
                    )
                    if ended_by_id
                    else None
                ),
                include_internal=(
                    include_internal
                ),
            )
        )

    return items


# LIVE CO-HOST LOOKUPS
def get_active_live_cohost(
    live_id: str,
):
    """
    Return the currently active co-host row for a live stream.

    Public Live payloads must serialize this row with include_internal=False.
    """
    if not live_id:
        return None

    return frappe.db.get_value(
        LIVE_COHOST_DOCTYPE,
        {
            "live_stream": live_id,
            "status": COHOST_STATUS_ACTIVE,
            "is_active": 1,
        },
        live_cohost_fields(),
        as_dict=True,
    )


def get_viewer_live_cohost_workflow(
    *,
    live_id: str,
    viewer: str | None,
):
    """
    Return the authenticated viewer's unresolved co-host workflow.

    Guests never have co-host workflows.
    """
    if (
        not live_id
        or is_guest_user(viewer)
    ):
        return None

    return frappe.db.get_value(
        LIVE_COHOST_DOCTYPE,
        {
            "live_stream": live_id,
            "user": viewer,
            "status": [
                "in",
                list(COHOST_UNRESOLVED_STATUSES),
            ],
        },
        live_cohost_fields(),
        as_dict=True,
    )


def preload_active_live_cohosts(
    live_ids: list[str],
) -> dict[str, dict]:
    """
    Batch preload active co-hosts for Live list serialization.

    Returned payloads are always public and exclude session/LiveKit fields.
    """
    live_ids = sorted(
        {
            live_id
            for live_id in live_ids
            if live_id
        }
    )

    if not live_ids:
        return {}

    rows = frappe.get_all(
        LIVE_COHOST_DOCTYPE,
        filters={
            "live_stream": [
                "in",
                live_ids,
            ],
            "status": COHOST_STATUS_ACTIVE,
            "is_active": 1,
        },
        fields=live_cohost_fields(),
        order_by="started_at desc, creation desc",
    )

    result: dict[str, dict] = {}

    for row in rows:
        live_id = _value(
            row,
            "live_stream",
        )

        if live_id in result:
            continue

        result[live_id] = serialize_live_cohost(
            row,
            include_internal=False,
        )

    return result


# VIEW SESSION HELPERS
def has_active_view_session(
    *,
    live_id: str,
    viewer: str | None = None,
    session_id: str | None = None,
) -> bool:
    """
    Return whether this exact viewer/session has an active view row.

    Authenticated sessions are constrained to the authenticated user.
    Guest sessions are constrained to rows with no user.
    """
    if not live_id:
        return False

    filters = {
        "live_stream": live_id,
        "is_active": 1,
    }

    if session_id:
        filters["session_id"] = (
            session_id
        )

        _apply_viewer_ownership_filter(
            filters=filters,
            viewer=viewer,
        )

    elif (
        viewer
        and not is_guest_user(viewer)
    ):
        filters["user"] = viewer

    else:
        return False

    return bool(
        frappe.db.exists(
            LIVE_VIEW_DOCTYPE,
            filters,
        )
    )


# RELATIONSHIP / VIEWER STATE
def guest_relationship_payload(
    host_user: str | None,
) -> dict:
    available = bool(host_user)
    return {
        "target_user": public_account_id_for_user(host_user),
        "is_self": False,
        "is_following": False,
        "is_followed_by": False,
        "is_friend": False,
        "relationship_status": "none",
        "action_label": "Follow",
        "is_blocked_by_me": False,
        "has_blocked_me": False,
        "is_blocked": False,
        "block_status": "none",
        "can_follow": False,
        "can_message": False,
        "can_call": False,
        "can_view_profile": available,
    }


def build_live_viewer_state(
    *,
    live,
    viewer: str | None,
    session_id: str | None = None,
    preloaded_relationship: dict | None = None,
    preloaded_has_joined: bool | None = None,
    preloaded_cohost_workflow: dict | None = None,
) -> dict:
    """
    Build viewer-specific Live state.

    Guests skip relationship database logic. Logged-in viewers receive
    relationship state against live.host_user.
    """
    live_id = _value(
        live,
        "name",
    )

    host_user = _value(
        live,
        "host_user",
    )

    status = _value(
        live,
        "status",
    )

    is_active = _as_bool(
        _value(
            live,
            "is_active",
        )
    )

    guest = is_guest_user(
        viewer
    )

    is_host = bool(
        viewer
        and host_user
        and viewer == host_user
    )

    can_watch = (
        status == LIVE_STATUS
        and is_active
    )

    if guest:
        relationship = (
            guest_relationship_payload(
                host_user
            )
        )

    elif preloaded_relationship is not None:
        relationship = dict(
            preloaded_relationship
        )

    else:
        relationship = SocialCapabilityService().relationship_projection(
            viewer=viewer,
            target=host_user,
        )

    if is_host:
        has_joined = True

    elif preloaded_has_joined is not None:
        has_joined = bool(
            preloaded_has_joined
        )

    else:
        has_joined = has_active_view_session(
            live_id=live_id,
            viewer=viewer,
            session_id=session_id,
        )

    if is_host:
        cohost_workflow = None
    elif preloaded_cohost_workflow is not None:
        cohost_workflow = dict(
            preloaded_cohost_workflow
        )
    else:
        cohost_workflow_row = (
            get_viewer_live_cohost_workflow(
                live_id=live_id,
                viewer=viewer,
            )
        )

        cohost_workflow = (
            serialize_live_cohost(
                cohost_workflow_row,
                include_internal=True,
            )
            if cohost_workflow_row
            else None
        )

    cohost_status = (
        cohost_workflow.get("status")
        if cohost_workflow
        else None
    )

    is_cohost = bool(
        cohost_status == COHOST_STATUS_ACTIVE
    )

    has_pending_cohost_workflow = bool(
        cohost_status in {
            COHOST_STATUS_PENDING,
            COHOST_STATUS_ACCEPTED,
            COHOST_STATUS_ACTIVE,
        }
    )

    can_interact = bool(
        viewer
        and not guest
        and can_watch
        and (
            is_host
            or has_joined
        )
    )

    relationship.update(
        {
            "is_owner": is_host,
            "is_host": is_host,
            "has_joined": bool(
                has_joined
            ),
            "can_join": bool(
                can_watch
            ),
            "can_watch": bool(
                can_watch
            ),
            "can_comment": bool(
                can_interact
            ),
            "can_react": bool(
                can_interact
            ),
            "can_end": bool(
                is_host
                and can_watch
            ),
            "can_report": bool(
                can_interact
                and not is_host
            ),
            "is_cohost": is_cohost,
            "cohost_status": cohost_status,
            "cohost_workflow": cohost_workflow,
            "can_request_cohost": bool(
                can_interact
                and not is_host
                and not has_pending_cohost_workflow
            ),
            "can_invite_cohost": bool(
                is_host
                and can_watch
            ),
        }
    )

    return relationship


# LIVE SERIALIZATION

def _live_cover_media_url(media_id) -> str:
    if not media_id:
        return ""

    try:
        from aos.api.live.media import get_public_media_url

        return get_public_media_url(media_id)
    except Exception:
        return ""

def serialize_live(
    live,
    *,
    viewer: str | None = None,
    session_id: str | None = None,
    preloaded_host: dict | None = None,
    preloaded_viewer_state: dict | None = None,
    preloaded_relationship: dict | None = None,
    preloaded_has_joined: bool | None = None,
    preloaded_active_cohost: dict | None = None,
    preloaded_cohost_workflow: dict | None = None,
) -> dict:
    """
    Serialize one AOS Live Stream row or document.

    Supports Frappe Documents and dictionary rows returned by database
    queries.
    """
    live_id = _value(
        live,
        "name",
    )

    host_user = _value(
        live,
        "host_user",
    )

    cover_image = _value(
        live,
        "cover_image",
    )

    cover_media_id = _value(
        live,
        "live_cover_media",
    )

    if not cover_image and cover_media_id:
        cover_image = _live_cover_media_url(cover_media_id)

    room_name = (
        _value(
            live,
            "room_name",
        )
        or f"live:{live_id}"
    )

    host = (
        preloaded_host
        or serialize_user(
            host_user
        )
    )

    if preloaded_active_cohost is not None:
        active_cohost = preloaded_active_cohost
    else:
        active_cohost_row = get_active_live_cohost(
            live_id
        )

        active_cohost = (
            serialize_live_cohost(
                active_cohost_row,
                include_internal=False,
            )
            if active_cohost_row
            else None
        )

    payload = {
        "id": live_id,
        "live_id": live_id,
        "status": _value(
            live,
            "status",
        ),
        "title": _value(
            live,
            "title",
        ),
        "room_name": room_name,
        "viewer_count": _as_int(
            _value(
                live,
                "viewer_count",
            )
        ),
        "viewer_count_display": humanize_count(
            _as_int(
                _value(
                    live,
                    "viewer_count",
                )
            )
        ),
        "total_views": _as_int(
            _value(
                live,
                "total_views",
            )
        ),
        "total_views_display": humanize_count(
            _as_int(
                _value(
                    live,
                    "total_views",
                )
            )
        ),
        "total_joins": _as_int(_value(live, "total_joins")),
        "total_joins_display": humanize_count(_as_int(_value(live, "total_joins"))),
        "unique_viewers": _as_int(_value(live, "unique_viewers")),
        "unique_viewers_display": humanize_count(_as_int(_value(live, "unique_viewers"))),
        "peak_viewers": _as_int(
            _value(
                live,
                "peak_viewers",
            )
        ),
        "peak_viewers_display": humanize_count(
            _as_int(
                _value(
                    live,
                    "peak_viewers",
                )
            )
        ),
        "like_count": _as_int(
            _value(
                live,
                "like_count",
            )
        ),
        "like_count_display": humanize_count(
            _as_int(
                _value(
                    live,
                    "like_count",
                )
            )
        ),
        "reaction_count": _as_int(
            _value(
                live,
                "reaction_count",
            )
        ),
        "reaction_count_display": humanize_count(
            _as_int(
                _value(
                    live,
                    "reaction_count",
                )
            )
        ),
        "comment_count": _as_int(
            _value(
                live,
                "comment_count",
            )
        ),
        "comment_count_display": humanize_count(
            _as_int(
                _value(
                    live,
                    "comment_count",
                )
            )
        ),
        "total_watch_time_seconds": (
            _as_int(
                _value(
                    live,
                    "total_watch_time_seconds",
                )
            )
        ),
        "cover_image": cover_image,
        "cover_image_media": cover_media_id,
        "cover_image_media_id": cover_media_id,
        "live_cover_media": cover_media_id,
        "live_cover_media_id": cover_media_id,
        "thumbnail": cover_image,
        "started_at": _value(
            live,
            "started_at",
        ),
        "ended_at": _value(
            live,
            "ended_at",
        ),
        "duration_seconds": _as_int(
            _value(
                live,
                "duration_seconds",
            )
        ),
        "is_active": _as_bool(
            _value(
                live,
                "is_active",
            )
        ),

        # Compatibility flat host fields.
        "host_user": host["user"],
        "host_display_name": host[
            "display_name"
        ],
        "host_avatar": host[
            "avatar"
        ],
        "host_total_followers": _as_int(
            host.get(
                "total_followers"
            )
        ),
        "host_total_followers_display": host.get(
            "total_followers_display"
        ) or humanize_count(
            host.get(
                "total_followers"
            )
        ),

        # Preferred structured host payload.
        "host": host,

        # Public active co-host state.
        "active_cohost": active_cohost,
        "has_active_cohost": bool(
            active_cohost
        ),
    }

    payload["viewer_state"] = (
        preloaded_viewer_state
        or build_live_viewer_state(
            live=live,
            viewer=viewer,
            session_id=session_id,
            preloaded_relationship=(
                preloaded_relationship
            ),
            preloaded_has_joined=(
                preloaded_has_joined
            ),
            preloaded_cohost_workflow=(
                preloaded_cohost_workflow
            ),
        )
    )

    return payload


# BATCH USER SERIALIZATION
def preload_users(
    users: list[str],
) -> dict[str, dict]:
    """Batch preload display-safe User and AOS Profile context."""
    users = sorted(
        {
            user
            for user in users
            if user
        }
    )

    if not users:
        return {}

    display_map = get_user_display_map(users)

    profile_rows = frappe.get_all(
        "AOS Profile",
        filters={
            "user": [
                "in",
                users,
            ],
        },
        fields=[
            "user",
            "is_verified",
            "total_followers",
        ],
    )

    profile_by_user = {
        row.user: row
        for row in profile_rows
    }

    result: dict[str, dict] = {}

    for user in users:
        display = display_map.get(user) or fallback_user_payload(user)
        is_deleted = bool(display.get("is_deleted"))
        profile = profile_by_user.get(user)

        result[user] = {
            "user": display.get("account_id"),
            "display_name": display.get("display_name"),
            "avatar": display.get("avatar"),
            "is_deleted": is_deleted,
            "is_live": bool(display.get("is_live")) if not is_deleted else False,
            "live_id": display.get("live_id") if not is_deleted else None,
            "live_status": display.get("live_status") if not is_deleted else None,
            "live_title": display.get("live_title") if not is_deleted else None,
            "live_cover_image": display.get("live_cover_image") if not is_deleted else None,
            "live_started_at": display.get("live_started_at") if not is_deleted else None,
            "live_viewer_count": int(display.get("live_viewer_count") or 0) if not is_deleted else 0,
            "live_viewer_count_display": humanize_count(
                int(display.get("live_viewer_count") or 0)
                if not is_deleted
                else 0
            ),
            "is_verified": (
                bool(profile.is_verified)
                if profile and not is_deleted
                else False
            ),
            "total_followers": (
                int(profile.total_followers or 0)
                if profile and not is_deleted
                else 0
            ),
            "total_followers_display": humanize_count(
                int(profile.total_followers or 0)
                if profile and not is_deleted
                else 0
            ),
        }

    return result


# RELATIONSHIP PRELOADING
def preload_relationships(
    *,
    viewer: str | None,
    target_users: list[str],
) -> dict[str, dict]:
    """Batch preload canonical, block-aware relationship state for Live."""
    targets = sorted({user for user in target_users if user})
    if not targets:
        return {}
    if is_guest_user(viewer):
        return {target: guest_relationship_payload(target) for target in targets}
    return social_relationship_map(
        repository=SocialRepository(),
        viewer=str(viewer),
        targets=targets,
    )


# JOINED-LIVE PRELOADING
def preload_joined_live_ids(
    *,
    viewer: str | None = None,
    session_id: str | None = None,
    live_ids: list[str],
) -> set[str]:
    """
    Batch preload active joined/watching state.

    Session-based lookups are constrained to the expected viewer owner.
    """
    live_ids = sorted(
        {
            live_id
            for live_id in live_ids
            if live_id
        }
    )

    if not live_ids:
        return set()

    filters = {
        "live_stream": [
            "in",
            live_ids,
        ],
        "is_active": 1,
    }

    if session_id:
        filters["session_id"] = (
            session_id
        )

        _apply_viewer_ownership_filter(
            filters=filters,
            viewer=viewer,
        )

    elif (
        viewer
        and not is_guest_user(viewer)
    ):
        filters["user"] = viewer

    else:
        return set()

    rows = frappe.get_all(
        LIVE_VIEW_DOCTYPE,
        filters=filters,
        fields=[
            "live_stream",
        ],
    )

    return {
        row.live_stream
        for row in rows
    }


# LIVE LIST SERIALIZATION
def serialize_live_list(
    lives: list,
    *,
    viewer: str | None = None,
    session_id: str | None = None,
) -> list[dict]:
    """
    Serialize Live feed results with batched:
    - user preloading
    - relationship preloading
    - active joined-state preloading
    """
    if not lives:
        return []

    host_users = [
        _value(
            live,
            "host_user",
        )
        for live in lives
        if _value(
            live,
            "host_user",
        )
    ]

    live_ids = [
        _value(
            live,
            "name",
        )
        for live in lives
        if _value(
            live,
            "name",
        )
    ]

    hosts = preload_users(
        host_users
    )

    relationships = preload_relationships(
        viewer=viewer,
        target_users=host_users,
    )

    joined_live_ids = (
        preload_joined_live_ids(
            viewer=viewer,
            session_id=session_id,
            live_ids=live_ids,
        )
    )

    active_cohosts = preload_active_live_cohosts(
        live_ids
    )

    items: list[dict] = []

    for live in lives:
        live_id = _value(
            live,
            "name",
        )

        host_user = _value(
            live,
            "host_user",
        )

        relationship = relationships.get(host_user)
        if relationship and relationship.get("is_blocked"):
            continue

        items.append(
            serialize_live(
                live,
                viewer=viewer,
                session_id=session_id,
                preloaded_host=hosts.get(
                    host_user
                ),
                preloaded_relationship=relationship,
                preloaded_has_joined=(
                    live_id
                    in joined_live_ids
                ),
                preloaded_active_cohost=(
                    active_cohosts.get(
                        live_id
                    )
                ),
            )
        )

    return items
