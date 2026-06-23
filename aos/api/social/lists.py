"""
Social follow lists.

Supports:
  - Following: users current_user follows
  - Followers: users following current_user
  - Friends: mutual follows
  - Optional search inside each list by user name/email
"""

from __future__ import annotations

import re

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.formatters import humanize_count, to_non_negative_int
from aos.api.shared.user_display import get_user_display_map

from .constants import (
    DEFAULT_SOCIAL_LIST_LIMIT,
    GET_FOLLOWERS_LIMIT_PER_MINUTE_PER_USER,
    GET_FOLLOWING_LIMIT_PER_MINUTE_PER_USER,
    GET_FRIENDS_LIMIT_PER_MINUTE_PER_USER,
    MAX_SOCIAL_LIST_LIMIT,
    SOCIAL_LIST_SEARCH_MAX_LEN,
    SOCIAL_LIST_SEARCH_MIN_LEN,
)
from .relationship import build_relationship_status


LIKE_ESCAPE_CHAR = "\\"


def get_following_impl(**kwargs):
    """
    Get users that current_user is following.

    Meaning:
      AOS Follow.follower_user = current_user
      AOS Follow.following_user = returned user

    Optional:
      search: filters returned users by full_name/first_name/user id.
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
        search, search_err = _get_search(kwargs)
        if search_err:
            return search_err

        search_sql, search_params = _build_user_search_filter(search)
        block_sql, block_params = _build_block_filter(
            current_user=current_user,
            target_expr="f.following_user",
        )

        row_params = [current_user, *block_params, *search_params, limit, start]
        rows = frappe.db.sql(
            f"""
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
            {block_sql}
            {search_sql}
            ORDER BY f.creation DESC
            LIMIT %s OFFSET %s
            """,
            row_params,
            as_dict=True,
        )

        total = _get_following_total(
            current_user=current_user,
            block_sql=block_sql,
            block_params=block_params,
            search_sql=search_sql,
            search_params=search_params,
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
                "total_display": humanize_count(total),
                "limit": limit,
                "start": start,
                "search": search or "",
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

    Optional:
      search: filters returned users by full_name/first_name/user id.
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
        search, search_err = _get_search(kwargs)
        if search_err:
            return search_err

        search_sql, search_params = _build_user_search_filter(search)
        block_sql, block_params = _build_block_filter(
            current_user=current_user,
            target_expr="f.follower_user",
        )

        row_params = [current_user, *block_params, *search_params, limit, start]
        rows = frappe.db.sql(
            f"""
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
            {block_sql}
            {search_sql}
            ORDER BY f.creation DESC
            LIMIT %s OFFSET %s
            """,
            row_params,
            as_dict=True,
        )

        total = _get_followers_total(
            current_user=current_user,
            block_sql=block_sql,
            block_params=block_params,
            search_sql=search_sql,
            search_params=search_params,
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
                "total_display": humanize_count(total),
                "limit": limit,
                "start": start,
                "search": search or "",
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

    Optional:
      search: filters returned users by full_name/first_name/user id.
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
        search, search_err = _get_search(kwargs)
        if search_err:
            return search_err

        search_sql, search_params = _build_user_search_filter(search)
        block_sql, block_params = _build_block_filter(
            current_user=current_user,
            target_expr="f1.following_user",
        )

        row_params = [current_user, *block_params, *search_params, limit, start]
        rows = frappe.db.sql(
            f"""
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
            {block_sql}
            {search_sql}
            ORDER BY GREATEST(f1.creation, f2.creation) DESC
            LIMIT %s OFFSET %s
            """,
            row_params,
            as_dict=True,
        )

        total_count = _get_friends_total(
            current_user=current_user,
            block_sql=block_sql,
            block_params=block_params,
            search_sql=search_sql,
            search_params=search_params,
        )

        users = _serialize_users(
            rows=rows,
            current_user=current_user,
        )

        return ok(
            "Friends fetched successfully.",
            data={
                "items": users,
                "total": total_count,
                "total_display": humanize_count(total_count),
                "limit": limit,
                "start": start,
                "search": search or "",
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
    display_map = get_user_display_map([row.get("user") for row in rows])

    for row in rows:
        target_user = row.get("user")
        display = display_map.get(target_user) or {}
        is_deleted = bool(display.get("is_deleted"))

        relationship = build_relationship_status(
            current_user=current_user,
            target_user=target_user,
        )

        total_followers = (
            to_non_negative_int(row.get("total_followers"))
            if not is_deleted
            else 0
        )
        total_following = (
            to_non_negative_int(row.get("total_following"))
            if not is_deleted
            else 0
        )

        item = {
            "user": target_user,
            "full_name": display.get("display_name") or row.get("full_name"),
            "user_image": display.get("avatar"),
            "is_deleted": is_deleted,
            "is_live": bool(display.get("is_live")) if not is_deleted else False,
            "live_id": display.get("live_id") if not is_deleted else None,
            "live_status": display.get("live_status") if not is_deleted else None,
            "live_title": display.get("live_title") if not is_deleted else None,
            "live_cover_image": display.get("live_cover_image") if not is_deleted else None,
            "total_followers": total_followers,
            "total_followers_display": humanize_count(total_followers),
            "total_following": total_following,
            "total_following_display": humanize_count(total_following),
            "is_verified": bool(row.get("is_verified")) if not is_deleted else False,
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


def _get_search(kwargs) -> tuple[str | None, dict | None]:
    search = re.sub(r"\s+", " ", str(kwargs.get("search") or "").strip())

    if not search:
        return None, None

    if len(search) < SOCIAL_LIST_SEARCH_MIN_LEN:
        return None, fail(
            f"Search must be at least {SOCIAL_LIST_SEARCH_MIN_LEN} characters.",
            code="VALIDATION_ERROR",
        )

    if len(search) > SOCIAL_LIST_SEARCH_MAX_LEN:
        return None, fail(
            f"Search is too long. Maximum is {SOCIAL_LIST_SEARCH_MAX_LEN} characters.",
            code="VALIDATION_ERROR",
        )

    return search, None


def _escape_like(value: str) -> str:
    return (
        value.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR * 2)
        .replace("%", LIKE_ESCAPE_CHAR + "%")
        .replace("_", LIKE_ESCAPE_CHAR + "_")
    )


def _build_user_search_filter(
    search: str | None,
    *,
    user_alias: str = "u",
    profile_alias: str = "p",
) -> tuple[str, list[str]]:
    """Build SQL and params for social-list user search.

    When a search term is supplied, deleted profiles are excluded so old private
    names/emails from deleted accounts cannot be matched by search.
    """
    if not search:
        return "", []

    like_value = f"%{_escape_like(search)}%"

    return (
        f"""
        AND IFNULL({profile_alias}.is_deleted, 0) = 0
        AND IFNULL({profile_alias}.account_status, 'Active') != 'Deleted'
        AND (
            IFNULL({user_alias}.full_name, '') LIKE %s ESCAPE '\\\\'
            OR IFNULL({user_alias}.first_name, '') LIKE %s ESCAPE '\\\\'
            OR {user_alias}.name LIKE %s ESCAPE '\\\\'
        )
        """,
        [like_value, like_value, like_value],
    )


def _build_block_filter(*, current_user: str, target_expr: str) -> tuple[str, list[str]]:
    """Exclude users blocked in either direction from social lists."""
    return (
        f"""
        AND NOT EXISTS (
            SELECT 1
            FROM `tabAOS User Block` b
            WHERE b.status = 'Active'
              AND (
                    (b.blocker_user = %s AND b.blocked_user = {target_expr})
                 OR (b.blocked_user = %s AND b.blocker_user = {target_expr})
              )
        )
        """,
        [current_user, current_user],
    )


def _get_following_total(
    *,
    current_user: str,
    block_sql: str,
    block_params: list[str],
    search_sql: str,
    search_params: list[str],
) -> int:
    rows = frappe.db.sql(
        f"""
        SELECT COUNT(*) AS total
        FROM `tabAOS Follow` f
        INNER JOIN `tabAOS Profile` p
            ON p.name = f.following_user
        INNER JOIN `tabUser` u
            ON u.name = f.following_user
        WHERE f.follower_user = %s
        {block_sql}
        {search_sql}
        """,
        [current_user, *block_params, *search_params],
        as_dict=True,
    )

    return int(rows[0].total or 0) if rows else 0


def _get_followers_total(
    *,
    current_user: str,
    block_sql: str,
    block_params: list[str],
    search_sql: str,
    search_params: list[str],
) -> int:
    rows = frappe.db.sql(
        f"""
        SELECT COUNT(*) AS total
        FROM `tabAOS Follow` f
        INNER JOIN `tabAOS Profile` p
            ON p.name = f.follower_user
        INNER JOIN `tabUser` u
            ON u.name = f.follower_user
        WHERE f.following_user = %s
        {block_sql}
        {search_sql}
        """,
        [current_user, *block_params, *search_params],
        as_dict=True,
    )

    return int(rows[0].total or 0) if rows else 0


def _get_friends_total(
    *,
    current_user: str,
    block_sql: str,
    block_params: list[str],
    search_sql: str,
    search_params: list[str],
) -> int:
    rows = frappe.db.sql(
        f"""
        SELECT COUNT(*) AS total
        FROM `tabAOS Follow` f1
        INNER JOIN `tabAOS Follow` f2
            ON f2.follower_user = f1.following_user
           AND f2.following_user = f1.follower_user
        INNER JOIN `tabAOS Profile` p
            ON p.name = f1.following_user
        INNER JOIN `tabUser` u
            ON u.name = f1.following_user
        WHERE f1.follower_user = %s
        {block_sql}
        {search_sql}
        """,
        [current_user, *block_params, *search_params],
        as_dict=True,
    )

    return int(rows[0].total or 0) if rows else 0
