"""Profile statistics helpers.

These helpers compute profile-only social metrics. Keep them out of the shared
user display helper so normal avatar payloads, chat lists, notifications, and
seller/storefront responses do not pay for profile-specific stats.
"""

from __future__ import annotations

from typing import Any, Iterable

import frappe

SHORT_DOCTYPE = "AOS Short"
SHORT_STATUS_READY = "ready"
SHORT_VISIBILITY_VISIBLE = "visible"


def _normalize_user(user: Any) -> str | None:
    value = str(user or "").strip()
    return value or None


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def get_user_short_likes_count(user: str | None) -> int:
    """Return total likes received on one user's public ready shorts.

    This means likes received by the user's own shorts, not the number of
    shorts that user has liked.

    Only public/visible ready shorts are counted so hidden, deleted, failed,
    and processing shorts do not inflate the public profile metric.
    """
    normalized_user = _normalize_user(user)
    if not normalized_user:
        return 0

    row = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(COALESCE(like_count, 0)), 0) AS total_likes
        FROM `tab{SHORT_DOCTYPE}`
        WHERE owner = %s
          AND status = %s
          AND visibility_status = %s
        """,
        (normalized_user, SHORT_STATUS_READY, SHORT_VISIBILITY_VISIBLE),
        as_dict=True,
    )

    if not row:
        return 0

    return _to_int(row[0].get("total_likes"))


def get_users_short_likes_count(users: Iterable[str]) -> dict[str, int]:
    """Batch-return total public short likes keyed by User.name.

    Useful if profile lists later need this metric. Every requested user is
    included in the returned map with a default value of 0.
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

    result = {user: 0 for user in unique_users}

    rows = frappe.db.sql(
        f"""
        SELECT
            owner AS user,
            COALESCE(SUM(COALESCE(like_count, 0)), 0) AS total_likes
        FROM `tab{SHORT_DOCTYPE}`
        WHERE owner IN %(users)s
          AND status = %(status)s
          AND visibility_status = %(visibility)s
        GROUP BY owner
        """,
        {
            "users": tuple(unique_users),
            "status": SHORT_STATUS_READY,
            "visibility": SHORT_VISIBILITY_VISIBLE,
        },
        as_dict=True,
    )

    for row in rows:
        user = _normalize_user(row.get("user"))
        if user in result:
            result[user] = _to_int(row.get("total_likes"))

    return result
