"""Shared live-state helpers.

These helpers answer whether a user is currently hosting an active live stream.
They are intentionally read-only and compute state from AOS Live Stream instead
of duplicating live flags on User/AOS Profile.
"""

from __future__ import annotations

from typing import Any, Iterable

import frappe
from frappe.utils import get_datetime_str

LIVE_STREAM_DOCTYPE = "AOS Live Stream"
LIVE_STATUS = "live"


def _empty_live_state() -> dict[str, Any]:
    return {
        "is_live": False,
        "live_id": None,
        "live_status": None,
        "live_title": None,
        "live_cover_image": None,
        "live_cover_media": None,
        "live_cover_media_id": None,
        "live_started_at": None,
        "live_viewer_count": 0,
    }


def _normalize_user(user: Any) -> str | None:
    value = str(user or "").strip()
    return value or None


def _serialize_live_row(row: Any | None) -> dict[str, Any]:
    if not row:
        return _empty_live_state()

    started_at = getattr(row, "started_at", None)

    return {
        "is_live": True,
        "live_id": row.name,
        "live_status": getattr(row, "status", LIVE_STATUS) or LIVE_STATUS,
        "live_title": getattr(row, "title", None),
        "live_cover_image": getattr(row, "cover_image", None),
        "live_cover_media": getattr(row, "live_cover_media", None),
        "live_cover_media_id": getattr(row, "live_cover_media", None),
        "live_started_at": get_datetime_str(started_at) if started_at else None,
        "live_viewer_count": int(getattr(row, "viewer_count", 0) or 0),
    }


def get_user_live_state(user: str | None) -> dict[str, Any]:
    """Return current live-state payload for one user.

    A user is considered live when they have an active AOS Live Stream where:
      - host_user = user
      - status = "live"
      - is_active = 1
    """
    normalized_user = _normalize_user(user)
    if not normalized_user:
        return _empty_live_state()

    live_map = get_users_live_state([normalized_user])
    return live_map.get(normalized_user) or _empty_live_state()


def get_users_live_state(users: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Batch-return live-state payloads keyed by User.name.

    This avoids N+1 queries when rendering feeds/lists with many avatars.
    Every requested user is included in the returned map with either an active
    live payload or the default non-live payload.
    """
    unique_users = sorted(
        {
            normalized
            for normalized in (_normalize_user(user) for user in users)
            if normalized
        }
    )

    if not unique_users:
        return {}

    result = {user: _empty_live_state() for user in unique_users}

    rows = frappe.get_all(
        LIVE_STREAM_DOCTYPE,
        filters={
            "host_user": ["in", unique_users],
            "status": LIVE_STATUS,
            "is_active": 1,
        },
        fields=[
            "name",
            "host_user",
            "title",
            "cover_image",
            "live_cover_media",
            "status",
            "started_at",
            "viewer_count",
        ],
        order_by="started_at desc, modified desc",
    )

    for row in rows:
        host_user = _normalize_user(getattr(row, "host_user", None))
        if not host_user or host_user not in result:
            continue

        # AOS Live Stream validation should allow only one active live per host.
        # If old data ever contains duplicates, keep the newest row due to
        # order_by above and ignore the rest.
        if result[host_user].get("is_live"):
            continue

        result[host_user] = _serialize_live_row(row)

    return result
