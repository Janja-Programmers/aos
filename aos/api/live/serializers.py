"""
Live Stream serializers.

Responsibilities:
- Keep stable backend IDs internally.
- Return display-ready API/realtime payloads externally.
- Compute viewer-specific state for Live responses.
- Avoid storing duplicated display data on Live DocTypes.

Canonical ownership:
- AOS Live Stream.host_user = live host / creator user

Guest policy:
- Guests can watch live streams.
- Guests cannot comment, react, report, follow, or end lives.
"""

from __future__ import annotations

import frappe

from aos.api.social.relationship import build_relationship_status


LIVE_STATUS = "live"


# USER DISPLAY
def get_user_display(user: str | None) -> dict:
    """
    Return display-ready User context.

    Do not store these values on Live DocTypes. They are computed from User.
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
        ["name", "full_name", "user_image"],
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
    Return optional profile/social context for a user.

    Missing profile should not break Live serialization.
    """
    if not user:
        return {
            "is_verified": False,
            "total_followers": 0,
        }

    profile = frappe.db.get_value(
        "AOS Profile",
        user,
        ["is_verified", "total_followers"],
        as_dict=True,
    )

    if not profile:
        return {
            "is_verified": False,
            "total_followers": 0,
        }

    return {
        "is_verified": bool(profile.is_verified),
        "total_followers": int(profile.total_followers or 0),
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


# GENERIC HELPERS
def _value(row, fieldname: str, default=None):
    if isinstance(row, dict):
        return row.get(fieldname, default)

    return getattr(row, fieldname, default)


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _as_bool(value) -> bool:
    return bool(value)


def is_guest_user(user: str | None) -> bool:
    return not user or user == "Guest"


# VIEW SESSION HELPERS
def has_active_view_session(
    *,
    live_id: str,
    viewer: str | None = None,
    session_id: str | None = None,
) -> bool:
    """
    Returns whether this viewer/session currently has an active view row.

    session_id is preferred because guests do not have a user and logged-in
    viewers also carry a session id.
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

    return bool(frappe.db.exists("AOS Live Stream View", filters))


# RELATIONSHIP / VIEWER STATE
def guest_relationship_payload(host_user: str | None) -> dict:
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

    For guests, relationship DB logic is skipped.
    For logged-in viewers, relationship is computed against live.host_user.
    """
    live_id = _value(live, "name")
    host_user = _value(live, "host_user")
    status = _value(live, "status")
    is_active = _as_bool(_value(live, "is_active"))

    guest = is_guest_user(viewer)
    is_host = bool(viewer and host_user and viewer == host_user)
    can_watch = status == LIVE_STATUS and is_active

    if guest:
        relationship = guest_relationship_payload(host_user)
    elif preloaded_relationship is not None:
        relationship = dict(preloaded_relationship)
    else:
        relationship = build_relationship_status(
            current_user=viewer,
            target_user=host_user,
        )

    if is_host:
        has_joined = True
    elif preloaded_has_joined is not None:
        has_joined = bool(preloaded_has_joined)
    else:
        has_joined = has_active_view_session(
            live_id=live_id,
            viewer=viewer,
            session_id=session_id,
        )

    can_interact = bool(viewer) and not guest and can_watch

    relationship.update(
        {
            "is_owner": is_host,
            "is_host": is_host,
            "has_joined": bool(has_joined),
            "can_join": bool(can_watch),
            "can_watch": bool(can_watch),
            "can_comment": bool(can_interact),
            "can_react": bool(can_interact),
            "can_end": bool(is_host and can_watch),
            "can_report": bool(can_interact and not is_host),
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
    Serialize one AOS Live Stream row/doc.

    Supports both Frappe docs and dict rows from frappe.get_all/get_value.
    """
    live_id = _value(live, "name")
    host_user = _value(live, "host_user")
    cover_image = _value(live, "cover_image")
    room_name = _value(live, "room_name") or f"live:{live_id}"

    host = preloaded_host or serialize_user(host_user)

    payload = {
        "id": live_id,
        "live_id": live_id,
        "status": _value(live, "status"),
        "title": _value(live, "title"),
        "room_name": room_name,
        "viewer_count": _as_int(_value(live, "viewer_count")),
        "total_views": _as_int(_value(live, "total_views")),
        "peak_viewers": _as_int(_value(live, "peak_viewers")),
        "like_count": _as_int(_value(live, "like_count")),
        "reaction_count": _as_int(_value(live, "reaction_count")),
        "comment_count": _as_int(_value(live, "comment_count")),
        "total_watch_time_seconds": _as_int(_value(live, "total_watch_time_seconds")),
        "cover_image": cover_image,
        "thumbnail": cover_image,
        "started_at": _value(live, "started_at"),
        "ended_at": _value(live, "ended_at"),
        "duration_seconds": _as_int(_value(live, "duration_seconds")),
        "is_active": _as_bool(_value(live, "is_active")),

        # Compatibility flat host fields.
        "host_user": host["user"],
        "host_display_name": host["display_name"],
        "host_avatar": host["avatar"],

        # Preferred structured host payload.
        "host": host,
    }

    payload["viewer_state"] = preloaded_viewer_state or build_live_viewer_state(
        live=live,
        viewer=viewer,
        session_id=session_id,
        preloaded_relationship=preloaded_relationship,
        preloaded_has_joined=preloaded_has_joined,
    )

    return payload


# BATCH SERIALIZATION HELPERS
def preload_users(users: list[str]) -> dict[str, dict]:
    """
    Batch preload User + AOS Profile context for list endpoints.
    """
    users = sorted({u for u in users if u})

    if not users:
        return {}

    user_rows = frappe.get_all(
        "User",
        filters={"name": ["in", users]},
        fields=["name", "full_name", "user_image"],
    )

    profile_rows = frappe.get_all(
        "AOS Profile",
        filters={"user": ["in", users]},
        fields=["user", "is_verified", "total_followers"],
    )

    profile_by_user = {
        row.user: row
        for row in profile_rows
    }

    result: dict[str, dict] = {}

    for row in user_rows:
        profile = profile_by_user.get(row.name)

        result[row.name] = {
            "user": row.name,
            "display_name": row.full_name or row.name,
            "avatar": row.user_image,
            "is_verified": bool(profile.is_verified) if profile else False,
            "total_followers": int(profile.total_followers or 0) if profile else 0,
        }

    # Fallback for missing/deleted users so serialization never crashes.
    for user in users:
        result.setdefault(
            user,
            {
                "user": user,
                "display_name": user,
                "avatar": None,
                "is_verified": False,
                "total_followers": 0,
            },
        )

    return result


def preload_relationships(
    *,
    viewer: str | None,
    target_users: list[str],
) -> dict[str, dict]:
    """
    Batch preload relationship state for list endpoints.

    Produces the same shape as build_relationship_status for each target user,
    without doing per-row DB checks.
    """
    target_users = sorted({u for u in target_users if u})

    if is_guest_user(viewer) or not target_users:
        return {
            target: guest_relationship_payload(target)
            for target in target_users
        }

    following_rows = frappe.get_all(
        "AOS Follow",
        filters={
            "follower_user": viewer,
            "following_user": ["in", target_users],
        },
        fields=["following_user"],
    )

    followed_by_rows = frappe.get_all(
        "AOS Follow",
        filters={
            "following_user": viewer,
            "follower_user": ["in", target_users],
        },
        fields=["follower_user"],
    )

    following = {row.following_user for row in following_rows}
    followed_by = {row.follower_user for row in followed_by_rows}

    result: dict[str, dict] = {}

    for target in target_users:
        is_self = target == viewer
        is_following = target in following
        is_followed_by = target in followed_by
        is_friend = is_following and is_followed_by

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
            "relationship_status": relationship_status,
            "action_label": action_label,
        }

    return result


def preload_joined_live_ids(
    *,
    viewer: str | None = None,
    session_id: str | None = None,
    live_ids: list[str],
) -> set[str]:
    """
    Batch preload active joined/watching state for list endpoints.
    """
    live_ids = sorted({live_id for live_id in live_ids if live_id})

    if not live_ids:
        return set()

    filters = {
        "live_stream": ["in", live_ids],
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

    return {row.live_stream for row in rows}


def serialize_live_list(
    lives: list,
    *,
    viewer: str | None = None,
    session_id: str | None = None,
) -> list[dict]:
    """
    Serialize Live list/feed results with batched user, relationship, and joined-state preloads.
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
        live_id = _value(live, "name")
        host_user = _value(live, "host_user")

        items.append(
            serialize_live(
                live,
                viewer=viewer,
                session_id=session_id,
                preloaded_host=hosts.get(host_user),
                preloaded_relationship=relationships.get(host_user),
                preloaded_has_joined=live_id in joined_live_ids,
            )
        )

    return items
