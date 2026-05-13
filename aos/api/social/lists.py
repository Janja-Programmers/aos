"""
Social follow lists.

Supports:
  - Following: users current_user follows
  - Followers: users following current_user
  - Friends: mutual follows
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import (
    DEFAULT_SOCIAL_LIST_LIMIT,
    GET_FOLLOWERS_LIMIT_PER_MINUTE_PER_USER,
    GET_FOLLOWING_LIMIT_PER_MINUTE_PER_USER,
    GET_FRIENDS_LIMIT_PER_MINUTE_PER_USER,
    MAX_SOCIAL_LIST_LIMIT,
)
from .relationship import build_relationship_status


def get_following_impl(**kwargs):
    """
    Get users that current_user is following.

    Meaning:
      AOS Follow.follower_user = current_user
      AOS Follow.following_user = returned user
    """

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:follow:following:user:{current_user}",
        ttl_seconds=60,
        limit=GET_FOLLOWING_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        limit = _get_limit(kwargs)
        start = _get_start(kwargs)

        rows = frappe.db.sql(
            """
            SELECT
                f.following_user AS user,
                f.creation AS followed_at,

                p.total_followers,
                p.total_following,
                p.is_verified,

                u.full_name,
                u.user_image
            FROM `tabAOS Follow` f
            INNER JOIN `tabAOS Profile` p
                ON p.name = f.following_user
            INNER JOIN `tabUser` u
                ON u.name = f.following_user
            WHERE f.follower_user = %s
            ORDER BY f.creation DESC
            LIMIT %s OFFSET %s
            """,
            (current_user, limit, start),
            as_dict=True,
        )

        total = frappe.db.count(
            "AOS Follow",
            filters={
                "follower_user": current_user,
            },
        )

        users = _serialize_users(
            rows=rows,
            current_user=current_user,
        )

        return ok(
            "Following fetched successfully.",
            data={
                "items": users,
                "total": total,
                "limit": limit,
                "start": start,
                "has_more": start + len(users) < total,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Following Failed",
        )
        return fail("Failed to fetch following.", code="INTERNAL_ERROR")


def get_followers_impl(**kwargs):
    """
    Get users following current_user.

    Meaning:
      AOS Follow.following_user = current_user
      AOS Follow.follower_user = returned user
    """

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:follow:followers:user:{current_user}",
        ttl_seconds=60,
        limit=GET_FOLLOWERS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        limit = _get_limit(kwargs)
        start = _get_start(kwargs)

        rows = frappe.db.sql(
            """
            SELECT
                f.follower_user AS user,
                f.creation AS followed_at,

                p.total_followers,
                p.total_following,
                p.is_verified,

                u.full_name,
                u.user_image
            FROM `tabAOS Follow` f
            INNER JOIN `tabAOS Profile` p
                ON p.name = f.follower_user
            INNER JOIN `tabUser` u
                ON u.name = f.follower_user
            WHERE f.following_user = %s
            ORDER BY f.creation DESC
            LIMIT %s OFFSET %s
            """,
            (current_user, limit, start),
            as_dict=True,
        )

        total = frappe.db.count(
            "AOS Follow",
            filters={
                "following_user": current_user,
            },
        )

        users = _serialize_users(
            rows=rows,
            current_user=current_user,
        )

        return ok(
            "Followers fetched successfully.",
            data={
                "items": users,
                "total": total,
                "limit": limit,
                "start": start,
                "has_more": start + len(users) < total,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Followers Failed",
        )
        return fail("Failed to fetch followers.", code="INTERNAL_ERROR")


def get_friends_impl(**kwargs):
    """
    Get mutual follows for current_user.

    Meaning:
      current_user follows returned user
      AND returned user follows current_user
    """

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:follow:friends:user:{current_user}",
        ttl_seconds=60,
        limit=GET_FRIENDS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        limit = _get_limit(kwargs)
        start = _get_start(kwargs)

        rows = frappe.db.sql(
            """
            SELECT
                f1.following_user AS user,
                f1.creation AS followed_at,
                f2.creation AS followed_back_at,

                p.total_followers,
                p.total_following,
                p.is_verified,

                u.full_name,
                u.user_image
            FROM `tabAOS Follow` f1
            INNER JOIN `tabAOS Follow` f2
                ON f2.follower_user = f1.following_user
               AND f2.following_user = f1.follower_user
            INNER JOIN `tabAOS Profile` p
                ON p.name = f1.following_user
            INNER JOIN `tabUser` u
                ON u.name = f1.following_user
            WHERE f1.follower_user = %s
            ORDER BY GREATEST(f1.creation, f2.creation) DESC
            LIMIT %s OFFSET %s
            """,
            (current_user, limit, start),
            as_dict=True,
        )

        total = frappe.db.sql(
            """
            SELECT COUNT(*) AS total
            FROM `tabAOS Follow` f1
            INNER JOIN `tabAOS Follow` f2
                ON f2.follower_user = f1.following_user
               AND f2.following_user = f1.follower_user
            WHERE f1.follower_user = %s
            """,
            (current_user,),
            as_dict=True,
        )

        total_count = int(total[0].total or 0) if total else 0

        users = _serialize_users(
            rows=rows,
            current_user=current_user,
        )

        return ok(
            "Friends fetched successfully.",
            data={
                "items": users,
                "total": total_count,
                "limit": limit,
                "start": start,
                "has_more": start + len(users) < total_count,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Friends Failed",
        )
        return fail("Failed to fetch friends.", code="INTERNAL_ERROR")


# HELPERS
def _serialize_users(*, rows: list[dict], current_user: str) -> list[dict]:
    items = []

    for row in rows:
        target_user = row.get("user")

        relationship = build_relationship_status(
            current_user=current_user,
            target_user=target_user,
        )

        item = {
            "user": target_user,
            "full_name": row.get("full_name"),
            "user_image": row.get("user_image"),
            "total_followers": int(row.get("total_followers") or 0),
            "total_following": int(row.get("total_following") or 0),
            "is_verified": bool(row.get("is_verified")),
            "followed_at": row.get("followed_at"),
            **relationship,
        }

        if row.get("followed_back_at"):
            item["followed_back_at"] = row.get("followed_back_at")

        items.append(item)

    return items


def _get_limit(kwargs) -> int:
    try:
        limit = int(kwargs.get("limit") or DEFAULT_SOCIAL_LIST_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_SOCIAL_LIST_LIMIT

    if limit <= 0:
        return DEFAULT_SOCIAL_LIST_LIMIT

    return min(limit, MAX_SOCIAL_LIST_LIMIT)


def _get_start(kwargs) -> int:
    try:
        start = int(kwargs.get("start") or 0)
    except (TypeError, ValueError):
        start = 0

    return max(start, 0)
