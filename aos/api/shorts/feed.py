"""
Feed APIs for Shorts.

Handles:
- For You feed
- Following feed
- Ad-specific feed
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login, current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from aos.api.shorts.validators import validate_limit
from aos.api.shorts.constants import (
    FEED_DEFAULT_LIMIT,
    FEED_MAX_LIMIT,
)

from aos.api.shorts.utils import (
    build_cursor_where_clause,
    build_time_id_cursor,
    serialize_short_row,
)


# COMMON
def _get_limit(kwargs):
    limit_input = kwargs.get("limit")
    return validate_limit(limit_input, FEED_DEFAULT_LIMIT, FEED_MAX_LIMIT)


def _build_response(rows):
    if not rows:
        return ok("Feed fetched.", data={"items": [], "next_cursor": None})

    items = [serialize_short_row(r) for r in rows]

    last = rows[-1]
    next_cursor = build_time_id_cursor(
        created_on=last.get("creation"),
        name=last.get("name"),
    )

    return ok(
        "Feed fetched.",
        data={
            "items": items,
            "next_cursor": next_cursor,
        },
    )


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

        where_cursor, params_cursor = build_cursor_where_clause(
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            SELECT
                s.name,
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
                s.seller,
                s.ad,

                sel.shop_name,
                sel.avatar AS seller_avatar,

                ad.title AS ad_title,
                ad.price AS ad_price,
                ad.currency AS ad_currency

            FROM `tabAOS Short` s
            LEFT JOIN `tabAOS Seller` sel ON sel.name = s.seller
            LEFT JOIN `tabAOS Ad` ad ON ad.name = s.ad

            WHERE
                s.status = 'ready'
                AND s.visibility_status = 'visible'
                {where_cursor}

            ORDER BY
                s.ranking_score DESC,
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (*params_cursor, limit),
            as_dict=True,
        )

        return _build_response(rows)

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

        where_cursor, params_cursor = build_cursor_where_clause(
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            SELECT
                s.name,
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
                s.seller,
                s.ad,

                sel.shop_name,
                sel.avatar AS seller_avatar,

                ad.title AS ad_title,
                ad.price AS ad_price,
                ad.currency AS ad_currency

            FROM `tabAOS Short` s
            INNER JOIN `tabAOS Seller Follow` f ON f.seller = s.seller
            LEFT JOIN `tabAOS Seller` sel ON sel.name = s.seller
            LEFT JOIN `tabAOS Ad` ad ON ad.name = s.ad

            WHERE
                f.follower = %s
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
                {where_cursor}

            ORDER BY
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (user, *params_cursor, limit),
            as_dict=True,
        )

        return _build_response(rows)

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

        where_cursor, params_cursor = build_cursor_where_clause(
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            SELECT
                s.name,
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
                s.seller,
                s.ad,

                sel.shop_name,
                sel.avatar AS seller_avatar,

                ad.title AS ad_title,
                ad.price AS ad_price,
                ad.currency AS ad_currency

            FROM `tabAOS Short` s
            LEFT JOIN `tabAOS Seller` sel ON sel.name = s.seller
            LEFT JOIN `tabAOS Ad` ad ON ad.name = s.ad

            WHERE
                s.ad = %s
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
                {where_cursor}

            ORDER BY
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (ad_id, *params_cursor, limit),
            as_dict=True,
        )

        return _build_response(rows)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "feed_by_ad failed")
        return fail("Failed to fetch ad feed", code="INTERNAL_ERROR")
