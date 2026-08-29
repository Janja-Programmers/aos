"""
Live realtime events.

Handles:
- Live lifecycle events
- Viewer count events
- Unified live message events
- User-targeted live message events
- Co-host workflow events
- Co-host public lifecycle events
- Immediate reaction events
- Display-ready lightweight payloads

Privacy:
- Viewer identities and session IDs are not broadcast to the live room.
- Room participants receive viewer-count changes only.
- Host-only activity is delivered through targeted events/messages.
- Private co-host payloads may contain session information only when sent
  directly to the host or co-host candidate.
- Public co-host payloads must never contain session IDs, LiveKit identities,
  or private metadata.
"""

from __future__ import annotations

import hashlib
import time

from collections.abc import Iterable

import frappe

from aos.api.shared.user_display import get_user_display
from aos.services.social.constants import MAX_SOCIAL_EVENT_FANOUT
from aos.services.social.repository import SocialRepository


# REALTIME EVENT NAMES
EVENT_LIVE_STARTED = "aos_live_started"
EVENT_LIVE_ENDED = "aos_live_ended"
EVENT_LIVE_VIEWER_COUNT = "aos_live_viewer_count"

EVENT_LIVE_MESSAGE = "aos_live_message"
EVENT_LIVE_MESSAGE_DELETED = "aos_live_message_deleted"

EVENT_LIVE_REACTION = "aos_live_reaction"

EVENT_COHOST_INVITED = "aos_live_cohost_invited"
EVENT_COHOST_REQUEST_RECEIVED = "aos_live_cohost_request_received"
EVENT_COHOST_ACCEPTED = "aos_live_cohost_accepted"
EVENT_COHOST_REJECTED = "aos_live_cohost_rejected"
EVENT_COHOST_CANCELLED = "aos_live_cohost_cancelled"
EVENT_COHOST_ACTIVATED = "aos_live_cohost_activated"
EVENT_COHOST_STARTED = "aos_live_cohost_started"
EVENT_COHOST_ENDED = "aos_live_cohost_ended"


# CHANNEL HELPERS
def live_channel(
    live_id: str,
) -> str:
    return f"live:{live_id}"


# GENERIC HELPERS
def _unique_users(
    users: Iterable[str | None],
) -> list[str]:
    """
    Return unique, non-empty users while preserving input order.
    """
    return list(
        dict.fromkeys(
            user
            for user in users
            if user
        )
    )


def _publish_to_user(
    *,
    event: str,
    user: str,
    message: dict,
):
    if (
        not event
        or not user
        or not message
    ):
        return

    frappe.publish_realtime(
        event=event,
        message=message,
        user=user,
        after_commit=True,
    )


def _publish_to_users(
    *,
    event: str,
    users: Iterable[str | None],
    message: dict,
):
    if (
        not event
        or not message
    ):
        return

    for user in _unique_users(
        users
    ):
        _publish_to_user(
            event=event,
            user=user,
            message=message,
        )


def _publish_to_live_room(
    *,
    event: str,
    live_id: str,
    message: dict,
):
    if (
        not event
        or not live_id
        or not message
    ):
        return

    frappe.publish_realtime(
        event=event,
        message=message,
        room=live_channel(
            live_id
        ),
        after_commit=True,
    )


# USER HELPERS
def _get_user_display(
    user: str | None,
) -> dict:
    return get_user_display(user)


def _get_followers(
    user: str,
) -> list[str]:
    """
    Return users following the supplied host.

    AOS Follow uses:
    - follower_user: user who follows
    - following_user: user being followed
    """
    if not user:
        return []

    return SocialRepository().list_active_followers_for_event(
        target=user,
        limit=MAX_SOCIAL_EVENT_FANOUT,
    )


# PAYLOAD HELPERS
def _build_live_payload(
    live,
) -> dict:
    host = _get_user_display(
        live.host_user
    )

    return {
        "live_id": live.name,
        "id": live.name,
        "host_user": host["user"],
        "host_display_name": host[
            "display_name"
        ],
        "host_avatar": host["avatar"],
        "host_is_deleted": bool(host.get("is_deleted")),
        "host_is_live": bool(host.get("is_live")) if not bool(host.get("is_deleted")) else False,
        "host_live_id": host.get("live_id") if not bool(host.get("is_deleted")) else None,
        "host_live_status": host.get("live_status") if not bool(host.get("is_deleted")) else None,
        "title": live.title,
        "cover_image": live.cover_image,
        "cover_image_media": getattr(live, "live_cover_media", None),
        "cover_image_media_id": getattr(live, "live_cover_media", None),
        "live_cover_media": getattr(live, "live_cover_media", None),
        "live_cover_media_id": getattr(live, "live_cover_media", None),
        "thumbnail": live.cover_image,
        "viewer_count": max(
            int(
                live.viewer_count
                or 0
            ),
            0,
        ),
        "status": live.status,
        "room_name": (
            live.room_name
            or f"live:{live.name}"
        ),
        "started_at": live.started_at,
        "ended_at": live.ended_at,
    }


def _build_live_message_payload(
    *,
    live_id: str,
    message: dict,
) -> dict:
    return {
        "live_id": live_id,
        "message": message,
    }


def _build_cohost_payload(
    *,
    live_id: str,
    cohost: dict,
) -> dict:
    return {
        "live_id": live_id,
        "cohost": cohost,
    }


# LIFECYCLE EVENTS
def publish_live_started(
    live,
):
    """
    Notify:
    - Followers of the host
    - The host's active devices
    - Clients already subscribed to the live room
    """
    payload = _build_live_payload(
        live
    )

    host_user = live.host_user

    followers = [
        user
        for user in _get_followers(
            host_user
        )
        if (
            user
            and user != host_user
        )
    ]

    _publish_to_users(
        event=EVENT_LIVE_STARTED,
        users=followers,
        message=payload,
    )

    if host_user:
        _publish_to_user(
            event=EVENT_LIVE_STARTED,
            user=host_user,
            message=payload,
        )

    _publish_to_live_room(
        event=EVENT_LIVE_STARTED,
        live_id=live.name,
        message=payload,
    )


def publish_live_ended(
    live,
):
    payload = _build_live_payload(
        live
    )

    _publish_to_live_room(
        event=EVENT_LIVE_ENDED,
        live_id=live.name,
        message=payload,
    )

    if live.host_user:
        _publish_to_user(
            event=EVENT_LIVE_ENDED,
            user=live.host_user,
            message=payload,
        )


# VIEWER COUNT EVENTS
def _viewer_count_gate_key(live_id: str) -> str:
    digest = hashlib.sha256(str(live_id or "").encode("utf-8")).hexdigest()[:24]
    return f"aos:live:viewer-count-publish:v1:{digest}"


def _publish_viewer_count_now(live_id: str, viewer_count: int) -> None:
    _publish_to_live_room(
        event=EVENT_LIVE_VIEWER_COUNT,
        live_id=live_id,
        message={
            "live_id": live_id,
            "viewer_count": max(int(viewer_count or 0), 0),
        },
    )


def publish_viewer_count(
    live_id: str,
    viewer_count: int,
):
    """Coalesce viewer-count fan-out while guaranteeing a trailing update.

    A viral join storm must not broadcast one room event per entrant. The first
    change in each one-second bucket is published immediately and schedules one
    trailing worker read of the durable counter. Terminal zero bypasses the
    throttle so clients see Live shutdown promptly.
    """
    if not live_id:
        return
    count = max(int(viewer_count or 0), 0)
    if count == 0:
        _publish_viewer_count_now(live_id, 0)
        return

    try:
        cache = frappe.cache()
        gate_key = _viewer_count_gate_key(live_id)
        sequence = int(cache.incr(gate_key) or 0)
        try:
            cache.expire(gate_key, 1)
        except Exception:
            pass
        if sequence > 1:
            return
    except Exception:
        _publish_viewer_count_now(live_id, count)
        return

    _publish_viewer_count_now(live_id, count)
    try:
        frappe.enqueue(
            "aos.tasks.live.publish_coalesced_viewer_count",
            queue="short",
            enqueue_after_commit=True,
            job_id=f"aos_live_viewer_count:{hashlib.sha256(str(live_id).encode()).hexdigest()[:24]}:{int(time.time())}",
            live_id=live_id,
            delay_seconds=0.35,
        )
    except Exception:
        # The immediate after-commit event already preserves correctness.
        pass


# LIVE MESSAGE EVENTS
def publish_live_message(
    live_id: str,
    message: dict,
):
    """
    Publish a message to every client subscribed to the live room.

    Use only for messages whose stored visibility permits viewer delivery.
    Private messages must use publish_live_message_to_user() or
    publish_live_message_to_users().
    """
    if (
        not live_id
        or not message
    ):
        return

    _publish_to_live_room(
        event=EVENT_LIVE_MESSAGE,
        live_id=live_id,
        message=_build_live_message_payload(
            live_id=live_id,
            message=message,
        ),
    )


def publish_live_message_to_user(
    *,
    user: str,
    live_id: str,
    message: dict,
):
    """
    Publish a live message only to one user's active devices.

    Used for:
    - Host-only startup messages
    - Host-only viewer-joined messages
    - Co-host invitations
    - Viewer co-host requests delivered to the host
    - Private co-host response messages
    """

    if (
        not user
        or not live_id
        or not message
    ):
        return

    _publish_to_user(
        event=EVENT_LIVE_MESSAGE,
        user=user,
        message=_build_live_message_payload(
            live_id=live_id,
            message=message,
        ),
    )


def publish_live_message_to_users(
    *,
    users: Iterable[str | None],
    live_id: str,
    message: dict,
):
    """
    Publish the same message to several specific users.

    Empty and duplicate recipients are removed.
    """
    if (
        not live_id
        or not message
    ):
        return

    _publish_to_users(
        event=EVENT_LIVE_MESSAGE,
        users=users,
        message=_build_live_message_payload(
            live_id=live_id,
            message=message,
        ),
    )


def publish_live_message_deleted(
    live_id: str,
    message_id: str,
    *,
    deleted_message_ids: list[str] | None = None,
):
    """
    Notify room participants that messages were soft-deleted.

    deleted_message_ids contains the selected message and any descendant
    replies deleted with it.
    """
    if (
        not live_id
        or not message_id
    ):
        return

    ids = list(
        dict.fromkeys(
            deleted_message_ids
            or [message_id]
        )
    )

    if message_id not in ids:
        ids.insert(
            0,
            message_id,
        )

    _publish_to_live_room(
        event=EVENT_LIVE_MESSAGE_DELETED,
        live_id=live_id,
        message={
            "live_id": live_id,
            "message_id": message_id,
            "deleted_message_ids": ids,
        },
    )


# PRIVATE CO-HOST WORKFLOW EVENTS
def publish_cohost_invited(
    *,
    user: str,
    live_id: str,
    cohost: dict,
):
    """
    Notify the invited viewer privately.

    The supplied payload may contain internal session fields because delivery
    is targeted to the candidate.
    """
    if (
        not user
        or not live_id
        or not cohost
    ):
        return

    _publish_to_user(
        event=EVENT_COHOST_INVITED,
        user=user,
        message=_build_cohost_payload(
            live_id=live_id,
            cohost=cohost,
        ),
    )


def publish_cohost_request_received(
    *,
    host_user: str,
    live_id: str,
    cohost: dict,
):
    """
    Notify the live host privately that a viewer requested co-host access.
    """
    if (
        not host_user
        or not live_id
        or not cohost
    ):
        return

    _publish_to_user(
        event=EVENT_COHOST_REQUEST_RECEIVED,
        user=host_user,
        message=_build_cohost_payload(
            live_id=live_id,
            cohost=cohost,
        ),
    )


def publish_cohost_accepted(
    *,
    users: Iterable[str | None],
    live_id: str,
    cohost: dict,
):
    """
    Notify the host and candidate privately that the workflow was accepted.
    """
    if (
        not live_id
        or not cohost
    ):
        return

    _publish_to_users(
        event=EVENT_COHOST_ACCEPTED,
        users=users,
        message=_build_cohost_payload(
            live_id=live_id,
            cohost=cohost,
        ),
    )


def publish_cohost_rejected(
    *,
    users: Iterable[str | None],
    live_id: str,
    cohost: dict,
):
    """
    Notify the host and candidate privately that the workflow was rejected.
    """
    if (
        not live_id
        or not cohost
    ):
        return

    _publish_to_users(
        event=EVENT_COHOST_REJECTED,
        users=users,
        message=_build_cohost_payload(
            live_id=live_id,
            cohost=cohost,
        ),
    )


def publish_cohost_cancelled(
    *,
    users: Iterable[str | None],
    live_id: str,
    cohost: dict,
):
    """
    Notify involved users privately that an invitation/request was cancelled.
    """
    if (
        not live_id
        or not cohost
    ):
        return

    _publish_to_users(
        event=EVENT_COHOST_CANCELLED,
        users=users,
        message=_build_cohost_payload(
            live_id=live_id,
            cohost=cohost,
        ),
    )


def publish_cohost_activated(
    *,
    users: Iterable[str | None],
    live_id: str,
    cohost: dict,
):
    """
    Notify the host and candidate privately that the co-host record became
    active.

    This payload may include internal session details because recipients are
    limited to workflow participants.
    """
    if (
        not live_id
        or not cohost
    ):
        return

    _publish_to_users(
        event=EVENT_COHOST_ACTIVATED,
        users=users,
        message=_build_cohost_payload(
            live_id=live_id,
            cohost=cohost,
        ),
    )


# PUBLIC CO-HOST LIFECYCLE EVENTS
def publish_cohost_started(
    *,
    live_id: str,
    cohost: dict,
):
    """
    Notify everyone in the live room that a co-host became active.

    The caller must supply a public serializer payload created using:

        serialize_live_cohost(..., include_internal=False)

    Do not include:
    - session_id
    - livekit_identity
    - private metadata
    """
    if (
        not live_id
        or not cohost
    ):
        return

    _publish_to_live_room(
        event=EVENT_COHOST_STARTED,
        live_id=live_id,
        message=_build_cohost_payload(
            live_id=live_id,
            cohost=cohost,
        ),
    )


def publish_cohost_ended(
    *,
    live_id: str,
    cohost: dict,
    private_users: Iterable[str | None] | None = None,
    private_cohost: dict | None = None,
):
    """
    Publish the public co-host-ended lifecycle event.

    Optionally publish a private version to the host and candidate using
    private_cohost. This avoids using a second, unnecessary event name.

    Public cohost:
    - Must exclude session and LiveKit identity.

    Private cohost:
    - May include internal session fields.
    """
    if (
        not live_id
        or not cohost
    ):
        return

    _publish_to_live_room(
        event=EVENT_COHOST_ENDED,
        live_id=live_id,
        message=_build_cohost_payload(
            live_id=live_id,
            cohost=cohost,
        ),
    )

    if (
        private_users
        and private_cohost
    ):
        _publish_to_users(
            event=EVENT_COHOST_ENDED,
            users=private_users,
            message=_build_cohost_payload(
                live_id=live_id,
                cohost=private_cohost,
            ),
        )


# REACTION EVENTS
def publish_reaction(
    *,
    live_id: str,
    reaction: dict,
):
    """
    Publish one canonical reaction immediately.

    The API response and realtime event use the same serialized payload.
    """
    if (
        not live_id
        or not reaction
    ):
        return

    _publish_to_live_room(
        event=EVENT_LIVE_REACTION,
        live_id=live_id,
        message={
            "live_id": live_id,
            "reaction": reaction,
        },
    )
