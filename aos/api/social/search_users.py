"""Global user search for social discovery.

This endpoint is intentionally separate from followers/following/friends list
search. It searches active AOS users globally and returns display-safe user
payloads with relationship state, deleted-user protection, and live-state fields.
"""

from __future__ import annotations

import re

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.sql_safety import (
    require_dotted_sql_identifier,
    safe_like_contains,
    safe_like_prefix,
)
from aos.api.shared.formatters import humanize_count, to_non_negative_int
from aos.api.shared.user_display import get_user_display_map

from .constants import (
    DEFAULT_USER_SEARCH_LIMIT,
    MAX_USER_SEARCH_LIMIT,
    SEARCH_USERS_LIMIT_PER_MINUTE_PER_USER,
    USER_SEARCH_MAX_LEN,
    USER_SEARCH_MIN_LEN,
)
from .relationship import build_relationship_status
from .activity import record_user_search_activity


SEARCHABLE_USER_FIELDS = (
    "u.full_name",
    "u.first_name",
    "u.name",
)


def search_users_impl(**kwargs):
    """Search active AOS users by name/user id.

    Request params:
      - query/search: required search text, min USER_SEARCH_MIN_LEN chars
      - limit: optional, defaults to DEFAULT_USER_SEARCH_LIMIT
      - start: optional offset, defaults to 0
    """

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:social:search_users:user:{current_user}",
        ttl_seconds=60,
        limit=SEARCH_USERS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many search requests. Please try again shortly.",
    )
    if rl:
        return rl

    query, err = _normalize_query(kwargs.get("query") or kwargs.get("search"))
    if err:
        return err

    limit = _get_limit(kwargs)
    start = _get_start(kwargs)

    try:
        rows = _search_user_rows(
            query=query,
            current_user=current_user,
            limit=limit,
            start=start,
        )
        total = _count_user_rows(
            query=query,
            current_user=current_user,
        )

        items = _serialize_search_rows(
            rows=rows,
            current_user=current_user,
        )

        record_user_search_activity(
            user=current_user,
            query=query,
            result_count=total,
        )

        return ok(
            "Users fetched successfully.",
            data={
                "items": items,
                "total": total,
                "limit": limit,
                "start": start,
                "query": query,
                "has_more": start + len(items) < total,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Search Users Failed",
        )
        return fail("Failed to search users.", code="INTERNAL_ERROR")


# HELPERS
def _normalize_query(value):
    query = re.sub(r"\s+", " ", str(value or "").strip())

    if len(query) < USER_SEARCH_MIN_LEN:
        return None, fail(
            f"Search query must be at least {USER_SEARCH_MIN_LEN} characters.",
            code="VALIDATION_ERROR",
        )

    if len(query) > USER_SEARCH_MAX_LEN:
        return None, fail(
            f"Search query is too long. Maximum is {USER_SEARCH_MAX_LEN} characters.",
            code="VALIDATION_ERROR",
        )

    return query, None


def _get_limit(kwargs) -> int:
    try:
        limit = int(kwargs.get("limit") or DEFAULT_USER_SEARCH_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_USER_SEARCH_LIMIT

    if limit <= 0:
        return DEFAULT_USER_SEARCH_LIMIT

    return min(limit, MAX_USER_SEARCH_LIMIT)


def _get_start(kwargs) -> int:
    try:
        start = int(kwargs.get("start") or 0)
    except (TypeError, ValueError):
        start = 0

    return max(start, 0)


def _search_like(query: str) -> str:
    return safe_like_contains(query)


def _prefix_like(query: str) -> str:
    return safe_like_prefix(query)


def _base_where_sql() -> str:
    search_parts = " OR ".join(
        f"{field} LIKE %s ESCAPE '\\\\'" for field in SEARCHABLE_USER_FIELDS
    )

    return f"""
        u.enabled = 1
        AND IFNULL(NULLIF(p.account_status, ''), 'Active') = 'Active'
        AND IFNULL(p.is_deleted, 0) = 0
        AND (
            {search_parts}
        )
    """


def _not_blocked_sql(*, target_expr: str) -> str:
    target_expr = require_dotted_sql_identifier(target_expr, label="blocked-user target expression")

    return f"""
        NOT EXISTS (
            SELECT 1
            FROM `tabAOS User Block` b
            WHERE b.status = 'Active'
              AND (
                    (b.blocker_user = %s AND b.blocked_user = {target_expr})
                 OR (b.blocked_user = %s AND b.blocker_user = {target_expr})
              )
        )
    """


def _search_params(query: str) -> tuple:
    search_value = _search_like(query)
    return tuple(search_value for _ in SEARCHABLE_USER_FIELDS)


def _search_user_rows(*, query: str, current_user: str, limit: int, start: int) -> list[dict]:
    search_params = _search_params(query)
    prefix_value = _prefix_like(query)

    return frappe.db.sql(
        f"""
        SELECT
            u.name AS user,
            u.full_name,
            u.first_name,
            u.user_image,

            p.total_followers,
            p.total_following,
            p.is_verified
        FROM `tabUser` u
        INNER JOIN `tabAOS Profile` p
            ON p.user = u.name
        WHERE {_base_where_sql()}
          AND {_not_blocked_sql(target_expr="u.name")}
        ORDER BY
            CASE
                WHEN u.full_name LIKE %s ESCAPE '\\\\' THEN 0
                WHEN u.first_name LIKE %s ESCAPE '\\\\' THEN 1
                WHEN u.name LIKE %s ESCAPE '\\\\' THEN 2
                ELSE 3
            END ASC,
            IFNULL(p.is_verified, 0) DESC,
            IFNULL(p.total_followers, 0) DESC,
            IFNULL(u.full_name, u.first_name) ASC,
            u.name ASC
        LIMIT %s OFFSET %s
        """,
        search_params
        + (
            current_user,
            current_user,
            prefix_value,
            prefix_value,
            prefix_value,
            limit,
            start,
        ),
        as_dict=True,
    )


def _count_user_rows(*, query: str, current_user: str) -> int:
    rows = frappe.db.sql(
        f"""
        SELECT COUNT(*) AS total
        FROM `tabUser` u
        INNER JOIN `tabAOS Profile` p
            ON p.user = u.name
        WHERE {_base_where_sql()}
          AND {_not_blocked_sql(target_expr="u.name")}
        """,
        _search_params(query) + (current_user, current_user),
        as_dict=True,
    )

    return int(rows[0].total or 0) if rows else 0


def _serialize_search_rows(*, rows: list[dict], current_user: str) -> list[dict]:
    items = []
    display_map = get_user_display_map([row.get("user") for row in rows])

    for row in rows:
        target_user = row.get("user")
        display = display_map.get(target_user) or {}
        is_deleted = bool(display.get("is_deleted"))

        # Search query already excludes deleted users. This guard keeps the
        # serializer safe if old data or future code passes one through.
        if is_deleted:
            continue

        relationship = build_relationship_status(
            current_user=current_user,
            target_user=target_user,
        )

        total_followers = to_non_negative_int(row.get("total_followers"))
        total_following = to_non_negative_int(row.get("total_following"))

        items.append(
            {
                "user": target_user,
                "full_name": display.get("display_name") or row.get("full_name"),
                "user_image": display.get("avatar"),
                "is_deleted": False,
                "is_live": bool(display.get("is_live")),
                "live_id": display.get("live_id"),
                "live_status": display.get("live_status"),
                "live_title": display.get("live_title"),
                "live_cover_image": display.get("live_cover_image"),
                "total_followers": total_followers,
                "total_followers_display": humanize_count(total_followers),
                "total_following": total_following,
                "total_following_display": humanize_count(total_following),
                "is_verified": bool(row.get("is_verified")),
                **relationship,
            }
        )

    return items
