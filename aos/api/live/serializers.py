"""
Live serializers.

Responsibilities:
- Keep stable backend IDs internally.
- Return display-ready API and realtime payloads externally.
- Compute viewer-specific state for Live responses.
- Serialize unified live messages.
- Avoid storing duplicated display data on Live DocTypes.

Canonical ownership:
- AOS Live Stream.host_user = live host / creator user

Live message model:
- AOS Live Message supports comments, replies, system events,
  co-host events, gifts, and moderation events.

Guest policy:
- Guests can watch live streams.
- Guests can read viewer-visible live messages.
- Guests cannot comment, react, report, follow, or end lives.
"""

from __future__ import annotations
from typing import Any

import frappe

from aos.api.social.relationship import build_relationship_status


LIVE_STATUS = "live"

LIVE_MESSAGE_DOCTYPE = "AOS Live Message"

LIVE_MESSAGE_KIND_COMMENT = "comment"
LIVE_MESSAGE_KIND_SYSTEM = "system"
LIVE_MESSAGE_KIND_COHOST = "cohost"
LIVE_MESSAGE_KIND_GIFT = "gift"
LIVE_MESSAGE_KIND_MODERATION = "moderation"

LIVE_MESSAGE_TYPE_COMMENT = "comment"
LIVE_MESSAGE_TYPE_REPLY = "reply"


# GENERIC HELPERS
def _value(
    row,
    fieldname: str,
    default=None,
):
    """
    Read a value from either:
    - a Frappe Document
    - a frappe._dict
    - a regular dictionary
    """

    if isinstance(row, dict):
        return row.get(fieldname, default)

    return getattr(row, fieldname, default)


def _as_int(
    value,
    default: int = 0,
) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _as_bool(value) -> bool:
    return bool(value)


def _parse_json_object(value: Any) -> dict:
    """
    Parse a value into a JSON object.

    Live message metadata must always be returned to clients as a dictionary.
    Invalid or missing data safely becomes an empty dictionary.
    """

    if value in (None, "", {}):
        return {}

    if isinstance(value, dict):
        return dict(value)

    try:
        parsed = frappe.parse_json(value)
    except Exception:
        return {}

    if not isinstance(parsed, dict):
        return {}

    return parsed


def is_guest_user(user: str | None) -> bool:
    return not user or user == "Guest"


# USER DISPLAY
def get_user_display(user: str | None) -> dict:
    """
    Return display-ready User context.

    These values should not be duplicated on Live DocTypes. They are
    calculated from the current User record.
    """
    if not user:
        return {
            "user": None,
            "display_name": None,
            "avatar": None,
        }

    row = frappe.db.get_value(
        "User",
        user,
        [
            "name",
            "full_name",
            "user_image",
        ],
        as_dict=True,
    )

    if not row:
        return {
            "user": user,
            "display_name": user,
            "avatar": None,
        }

    return {
        "user": row.name,
        "display_name": row.full_name or row.name,
        "avatar": row.user_image,
    }


def get_profile_context(user: str | None) -> dict:
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
        {"user": user},
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
        "is_verified": bool(profile.is_verified),
        "total_followers": int(
            profile.total_followers or 0
        ),
    }


def serialize_user(user: str | None) -> dict:
    display = get_user_display(user)
    profile = get_profile_context(user)

    return {
        "user": display["user"],
        "display_name": display["display_name"],
        "avatar": display["avatar"],
        "is_verified": profile["is_verified"],
        "total_followers": profile["total_followers"],
    }


def empty_user_payload() -> dict:
    return {
        "user": None,
        "display_name": None,
        "avatar": None,
        "is_verified": False,
        "total_followers": 0,
    }


def fallback_user_payload(user: str | None) -> dict:
    if not user:
        return empty_user_payload()

    return {
        "user": user,
        "display_name": user,
        "avatar": None,
        "is_verified": False,
        "total_followers": 0,
    }


# LIVE MESSAGE SERIALIZATION
def live_message_fields() -> list[str]:
    """
    Canonical fields used when querying AOS Live Message.

    All Live message endpoints should use this list to keep their payloads
    consistent.
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

    The payload shape is shared by:
    - add message responses
    - reply responses
    - list messages
    - list replies
    - realtime aos_live_message events
    """

    user_id = _value(message, "user")
    target_user_id = _value(message, "target_user")

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
            or fallback_user_payload(target_user_id)
        )
    else:
        target = None

    message_kind = _value(message, "message_kind")
    message_type = _value(message, "message_type")

    return {
        "id": _value(message, "name"),
        "message_id": _value(message, "name"),
        "live_stream": _value(message, "live_stream"),

        # Message classification.
        "kind": message_kind,
        "message_kind": message_kind,
        "message_type": message_type,

        # Actor identity.
        "user": actor["user"],
        "display_name": actor["display_name"],
        "avatar": actor["avatar"],
        "is_verified": bool(actor["is_verified"]),
        "total_followers": _as_int(
            actor.get("total_followers")
        ),
        "actor": actor if user_id else None,

        # Optional target identity.
        "target_user": target_user_id,
        "target": target,

        # Message content.
        "content": _value(message, "content"),
        "metadata": _parse_json_object(
            _value(message, "metadata_json")
        ),

        # Message state.
        "status": _value(message, "status"),
        "parent_message": _value(
            message,
            "parent_message",
        ),
        "root_message": _value(
            message,
            "root_message",
        ),
        "reply_count": _as_int(
            _value(message, "reply_count")
        ),

        # Visibility.
        "visible_to_host": _as_bool(
            _value(message, "visible_to_host")
        ),
        "visible_to_viewers": _as_bool(
            _value(message, "visible_to_viewers")
        ),

        # Convenient derived flags for Flutter.
        "is_comment": message_kind == LIVE_MESSAGE_KIND_COMMENT,
        "is_reply": (
            message_kind == LIVE_MESSAGE_KIND_COMMENT
            and message_type == LIVE_MESSAGE_TYPE_REPLY
        ),
        "is_system": message_kind == LIVE_MESSAGE_KIND_SYSTEM,
        "is_cohost_event": message_kind == LIVE_MESSAGE_KIND_COHOST,
        "is_gift": message_kind == LIVE_MESSAGE_KIND_GIFT,
        "is_moderation": (
            message_kind == LIVE_MESSAGE_KIND_MODERATION
        ),

        # Timestamps.
        "creation": _value(message, "creation"),
        "modified": _value(message, "modified"),
    }


def serialize_live_messages(
    messages: list,
) -> list[dict]:
    """
    Batch serialize AOS Live Message rows without per-message user queries.
    """

    if not messages:
        return []

    users_to_preload = {
        _value(message, "user")
        for message in messages
        if _value(message, "user")
    }

    users_to_preload.update(
        {
            _value(message, "target_user")
            for message in messages
            if _value(message, "target_user")
        }
    )

    users = preload_users(list(users_to_preload))

    items: list[dict] = []

    for message in messages:
        user_id = _value(message, "user")
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
                    users.get(target_user_id)
                    if target_user_id
                    else None
                ),
            )
        )

    return items


# VIEW SESSION HELPERS
def has_active_view_session(
    *,
    live_id: str,
    viewer: str | None = None,
    session_id: str | None = None,
) -> bool:
    """
    Return whether this viewer or session has an active view row.

    session_id is preferred because:
    - guests do not have a user
    - logged-in viewers also carry a session ID
    """
    if not live_id:
        return False

    filters = {
        "live_stream": live_id,
        "is_active": 1,
    }

    if session_id:
        filters["session_id"] = session_id
    elif viewer and not is_guest_user(viewer):
        filters["user"] = viewer
    else:
        return False

    return bool(
        frappe.db.exists(
            "AOS Live Stream View",
            filters,
        )
    )


# RELATIONSHIP / VIEWER STATE
def guest_relationship_payload(
    host_user: str | None,
) -> dict:
    return {
        "target_user": host_user,
        "is_self": False,
        "is_following": False,
        "is_followed_by": False,
        "is_friend": False,
        "relationship_status": "none",
        "action_label": "Follow",
    }


def build_live_viewer_state(
    *,
    live,
    viewer: str | None,
    session_id: str | None = None,
    preloaded_relationship: dict | None = None,
    preloaded_has_joined: bool | None = None,
) -> dict:
    """
    Build viewer-specific Live state.

    Guests skip relationship DB logic. Logged-in viewers receive relationship
    state against live.host_user.
    """
    live_id = _value(live, "name")
    host_user = _value(live, "host_user")
    status = _value(live, "status")
    is_active = _as_bool(
        _value(live, "is_active")
    )

    guest = is_guest_user(viewer)

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
        relationship = guest_relationship_payload(
            host_user
        )
    elif preloaded_relationship is not None:
        relationship = dict(
            preloaded_relationship
        )
    else:
        relationship = build_relationship_status(
            current_user=viewer,
            target_user=host_user,
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

    can_interact = bool(
        viewer
        and not guest
        and can_watch
    )

    relationship.update(
        {
            "is_owner": is_host,
            "is_host": is_host,
            "has_joined": bool(has_joined),
            "can_join": bool(can_watch),
            "can_watch": bool(can_watch),
            "can_comment": bool(can_interact),
            "can_react": bool(can_interact),
            "can_end": bool(
                is_host and can_watch
            ),
            "can_report": bool(
                can_interact and not is_host
            ),
        }
    )

    return relationship


# LIVE SERIALIZATION
def serialize_live(
    live,
    *,
    viewer: str | None = None,
    session_id: str | None = None,
    preloaded_host: dict | None = None,
    preloaded_viewer_state: dict | None = None,
    preloaded_relationship: dict | None = None,
    preloaded_has_joined: bool | None = None,
) -> dict:
    """
    Serialize one AOS Live Stream row or document.

    Supports both Frappe Documents and dictionary rows returned by
    frappe.get_all or frappe.db.get_value.
    """
    live_id = _value(live, "name")
    host_user = _value(live, "host_user")
    cover_image = _value(
        live,
        "cover_image",
    )

    room_name = (
        _value(live, "room_name")
        or f"live:{live_id}"
    )

    host = (
        preloaded_host
        or serialize_user(host_user)
    )

    payload = {
        "id": live_id,
        "live_id": live_id,
        "status": _value(live, "status"),
        "title": _value(live, "title"),
        "room_name": room_name,
        "viewer_count": _as_int(
            _value(live, "viewer_count")
        ),
        "total_views": _as_int(
            _value(live, "total_views")
        ),
        "peak_viewers": _as_int(
            _value(live, "peak_viewers")
        ),
        "like_count": _as_int(
            _value(live, "like_count")
        ),
        "reaction_count": _as_int(
            _value(live, "reaction_count")
        ),
        "comment_count": _as_int(
            _value(live, "comment_count")
        ),
        "total_watch_time_seconds": _as_int(
            _value(
                live,
                "total_watch_time_seconds",
            )
        ),
        "cover_image": cover_image,
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
            _value(live, "duration_seconds")
        ),
        "is_active": _as_bool(
            _value(live, "is_active")
        ),

        # Compatibility flat host fields.
        "host_user": host["user"],
        "host_display_name": host[
            "display_name"
        ],
        "host_avatar": host["avatar"],

        # Preferred structured host payload.
        "host": host,
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
        )
    )

    return payload


# BATCH USER SERIALIZATION
def preload_users(
    users: list[str],
) -> dict[str, dict]:
    """
    Batch preload User and AOS Profile context for list endpoints.
    """
    users = sorted(
        {
            user
            for user in users
            if user
        }
    )

    if not users:
        return {}

    user_rows = frappe.get_all(
        "User",
        filters={
            "name": ["in", users],
        },
        fields=[
            "name",
            "full_name",
            "user_image",
        ],
    )

    profile_rows = frappe.get_all(
        "AOS Profile",
        filters={
            "user": ["in", users],
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

    for row in user_rows:
        profile = profile_by_user.get(
            row.name
        )

        result[row.name] = {
            "user": row.name,
            "display_name": (
                row.full_name
                or row.name
            ),
            "avatar": row.user_image,
            "is_verified": (
                bool(profile.is_verified)
                if profile
                else False
            ),
            "total_followers": (
                int(
                    profile.total_followers
                    or 0
                )
                if profile
                else 0
            ),
        }

    # Fallback for missing or deleted users.
    for user in users:
        result.setdefault(
            user,
            fallback_user_payload(user),
        )

    return result


# RELATIONSHIP PRELOADING
def preload_relationships(
    *,
    viewer: str | None,
    target_users: list[str],
) -> dict[str, dict]:
    """
    Batch preload relationship state for Live list endpoints.

    Produces the same general shape as build_relationship_status without
    running per-row existence checks.
    """
    target_users = sorted(
        {
            user
            for user in target_users
            if user
        }
    )

    if is_guest_user(viewer) or not target_users:
        return {
            target: guest_relationship_payload(
                target
            )
            for target in target_users
        }

    following_rows = frappe.get_all(
        "AOS Follow",
        filters={
            "follower_user": viewer,
            "following_user": [
                "in",
                target_users,
            ],
        },
        fields=["following_user"],
    )

    followed_by_rows = frappe.get_all(
        "AOS Follow",
        filters={
            "following_user": viewer,
            "follower_user": [
                "in",
                target_users,
            ],
        },
        fields=["follower_user"],
    )

    following = {
        row.following_user
        for row in following_rows
    }

    followed_by = {
        row.follower_user
        for row in followed_by_rows
    }

    result: dict[str, dict] = {}

    for target in target_users:
        is_self = target == viewer
        is_following = target in following
        is_followed_by = target in followed_by
        is_friend = (
            is_following
            and is_followed_by
        )

        if is_self:
            relationship_status = "self"
            action_label = "You"
        elif is_friend:
            relationship_status = "friends"
            action_label = "Friends"
        elif is_following:
            relationship_status = "following"
            action_label = "Following"
        elif is_followed_by:
            relationship_status = "followed_by"
            action_label = "Follow Back"
        else:
            relationship_status = "none"
            action_label = "Follow"

        result[target] = {
            "target_user": target,
            "is_self": is_self,
            "is_following": is_following,
            "is_followed_by": is_followed_by,
            "is_friend": is_friend,
            "relationship_status": (
                relationship_status
            ),
            "action_label": action_label,
        }

    return result


# JOINED-LIVE PRELOADING
def preload_joined_live_ids(
    *,
    viewer: str | None = None,
    session_id: str | None = None,
    live_ids: list[str],
) -> set[str]:
    """
    Batch preload active joined/watching state for Live list endpoints.
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
        filters["session_id"] = session_id
    elif viewer and not is_guest_user(viewer):
        filters["user"] = viewer
    else:
        return set()

    rows = frappe.get_all(
        "AOS Live Stream View",
        filters=filters,
        fields=["live_stream"],
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
        _value(live, "host_user")
        for live in lives
        if _value(live, "host_user")
    ]

    live_ids = [
        _value(live, "name")
        for live in lives
        if _value(live, "name")
    ]

    hosts = preload_users(host_users)

    relationships = preload_relationships(
        viewer=viewer,
        target_users=host_users,
    )

    joined_live_ids = preload_joined_live_ids(
        viewer=viewer,
        session_id=session_id,
        live_ids=live_ids,
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

        items.append(
            serialize_live(
                live,
                viewer=viewer,
                session_id=session_id,
                preloaded_host=hosts.get(
                    host_user
                ),
                preloaded_relationship=(
                    relationships.get(
                        host_user
                    )
                ),
                preloaded_has_joined=(
                    live_id
                    in joined_live_ids
                ),
            )
        )

    return items
