"""
Feed APIs for Shorts.

Handles:
- For You feed
- Following feed
- Ad-specific feed
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from aos.api.shorts.validators import validate_limit, validate_content_mode
from aos.api.shorts.constants import (
    FEED_DEFAULT_LIMIT,
    FEED_MAX_LIMIT,
    SHORT_CONTENT_MODE_SHOP,
)

from aos.api.shorts.utils import (
    build_ranked_cursor,
    build_ranked_cursor_where_clause,
    serialize_short_row,
)


# COMMON
def _get_limit(kwargs):
    return validate_limit(
        kwargs.get("limit"),
        FEED_DEFAULT_LIMIT,
        FEED_MAX_LIMIT,
    )


def _build_content_mode_filter(content_mode):
    if not content_mode or str(content_mode).strip().lower() == "all":
        return "", (), None

    mode, err = validate_content_mode(content_mode)
    if err:
        return "", (), err

    return "AND s.content_mode = %s", (mode,), None


def _build_response(rows, limit: int):
    if not rows:
        return ok(
            "Feed fetched.",
            data={
                "items": [],
                "next_cursor": None,
                "has_more": False,
            },
        )

    has_more = len(rows) > limit
    visible_rows = rows[:limit]

    items = [serialize_short_row(r) for r in visible_rows]

    next_cursor = None
    if has_more:
        last = visible_rows[-1]
        next_cursor = build_ranked_cursor(
            ranking_score=last.get("ranking_score"),
            created_on=last.get("creation"),
            name=last.get("name"),
        )

    return ok(
        "Feed fetched.",
        data={
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
    )


def _select_short_rows_sql() -> str:
    return """
        SELECT
            s.name,
            s.owner,
            s.status,
            s.content_mode,
            s.caption,
            s.hashtags,
            s.playback_url,
            s.thumbnail_url,
            s.duration_seconds,
            s.view_count,
            s.like_count,
            s.comment_count,
            s.share_count,
            s.impression_count,
            s.ranking_score,
            s.posted_on,
            s.creation,
            s.seller,
            s.ad,

            u.full_name AS creator_name,
            u.user_image AS creator_avatar,
            COALESCE(p.is_verified, 0) AS creator_is_verified,

            ad.title AS ad_title,
            ad.price AS ad_price,
            ad.currency AS ad_currency

        FROM `tabAOS Short` s
        LEFT JOIN `tabUser` u ON u.name = s.owner
        LEFT JOIN `tabAOS Profile` p ON p.user = s.owner
        LEFT JOIN `tabAOS Ad` ad ON ad.name = s.ad
    """


# FEED: FOR YOU
def feed_for_you_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:feed:ip:{request_ip()}",
        ttl_seconds=60,
        limit=120,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        limit = _get_limit(kwargs)
        cursor = kwargs.get("cursor")

        mode_clause, mode_params, mode_err = _build_content_mode_filter(
            kwargs.get("content_mode") or kwargs.get("mode")
        )
        if mode_err:
            return mode_err

        where_cursor, params_cursor = build_ranked_cursor_where_clause(
            score_field="s.ranking_score",
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            {_select_short_rows_sql()}

            WHERE
                s.status = 'ready'
                AND s.visibility_status = 'visible'
                {mode_clause}
                {where_cursor}

            ORDER BY
                s.ranking_score DESC,
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (*mode_params, *params_cursor, limit + 1),
            as_dict=True,
        )

        return _build_response(rows, limit)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "feed_for_you failed")
        return fail("Failed to fetch feed", code="INTERNAL_ERROR")


# FEED: FOLLOWING
def feed_following_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:following:user:{user}",
        ttl_seconds=60,
        limit=120,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        limit = _get_limit(kwargs)
        cursor = kwargs.get("cursor")

        mode_clause, mode_params, mode_err = _build_content_mode_filter(
            kwargs.get("content_mode") or kwargs.get("mode")
        )
        if mode_err:
            return mode_err

        where_cursor, params_cursor = build_ranked_cursor_where_clause(
            score_field="s.ranking_score",
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            {_select_short_rows_sql()}
            INNER JOIN `tabAOS Follow` f ON f.following_user = s.owner

            WHERE
                f.follower_user = %s
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
                {mode_clause}
                {where_cursor}

            ORDER BY
                s.ranking_score DESC,
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (user, *mode_params, *params_cursor, limit + 1),
            as_dict=True,
        )

        return _build_response(rows, limit)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "feed_following failed")
        return fail("Failed to fetch following feed", code="INTERNAL_ERROR")


# FEED: BY AD
def feed_by_ad_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:by_ad:ip:{request_ip()}",
        ttl_seconds=60,
        limit=120,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    ad_id, err = require_id(kwargs.get("ad_id"), "ad_id")
    if err:
        return err

    try:
        limit = _get_limit(kwargs)
        cursor = kwargs.get("cursor")

        where_cursor, params_cursor = build_ranked_cursor_where_clause(
            score_field="s.ranking_score",
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            {_select_short_rows_sql()}

            WHERE
                s.ad = %s
                AND s.content_mode = %s
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
                {where_cursor}

            ORDER BY
                s.ranking_score DESC,
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (ad_id, SHORT_CONTENT_MODE_SHOP, *params_cursor, limit + 1),
            as_dict=True,
        )

        return _build_response(rows, limit)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "feed_by_ad failed")
        return fail("Failed to fetch ad feed", code="INTERNAL_ERROR")
