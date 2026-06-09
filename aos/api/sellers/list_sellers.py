"""
List Sellers.

Marketplace discovery endpoint for browsing sellers.
Lightweight version of the seller profile.

Returns exact numeric metrics together with compact display values,
including seller chat response metrics.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.formatters import (
    format_rating,
    humanize_count,
    to_float,
    to_non_negative_int,
)
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.social.relationship import build_relationship_status
from aos.services.seller_response_metrics import (
    format_response_rate,
    format_response_time,
)

from .constants import LIST_SELLERS_LIMIT_PER_MINUTE_PER_IP


DEFAULT_LIMIT = 20
MAX_LIMIT = 50

VALID_FOLLOW_FILTERS = {
    "following",
    "not_following",
}


def list_sellers_impl(**kwargs):
    """List active sellers for marketplace browsing."""

    try:
        rl = rate_limit(
            key="aos:sellers:list_sellers:ip",
            ttl_seconds=60,
            limit=LIST_SELLERS_LIMIT_PER_MINUTE_PER_IP,
            message="Too many requests. Please try again shortly.",
        )
        if rl:
            return rl

        limit = _get_limit(kwargs)
        offset = _get_offset(kwargs)

        search = _normalize_optional_string(
            kwargs.get("search")
        )
        seller_type = _normalize_optional_string(
            kwargs.get("seller_type")
        )
        business_category = _normalize_optional_string(
            kwargs.get("business_category")
            or kwargs.get("category")
        )
        follow_filter = _normalize_optional_string(
            kwargs.get("follow_filter")
        )

        is_verified_value, is_verified_error = (
            _get_verified_filter(
                kwargs.get("is_verified")
            )
        )
        if is_verified_error:
            return is_verified_error

        viewer = current_user()
        is_logged_in = bool(
            viewer
            and viewer != "Guest"
        )

        conditions = [
            "s.status = 'Active'",
        ]
        params: list[Any] = []

        # Do not show the current user's own seller profile in discovery.
        if is_logged_in:
            conditions.append("s.user != %s")
            params.append(viewer)

        if is_verified_value is not None:
            conditions.append(
                "COALESCE(p.is_verified, 0) = %s"
            )
            params.append(is_verified_value)

        if seller_type:
            conditions.append(
                "s.seller_type = %s"
            )
            params.append(seller_type)

        if business_category:
            conditions.append(
                "s.business_category = %s"
            )
            params.append(business_category)

        if search:
            conditions.append(
                """
                (
                    u.full_name LIKE %s
                    OR s.business_category LIKE %s
                    OR s.business_address LIKE %s
                    OR s.about_business LIKE %s
                )
                """
            )

            search_value = f"%{search}%"

            params.extend(
                [
                    search_value,
                    search_value,
                    search_value,
                    search_value,
                ]
            )

        follow_filter_error = _validate_follow_filter(
            follow_filter=follow_filter,
            is_logged_in=is_logged_in,
        )
        if follow_filter_error:
            return follow_filter_error

        if follow_filter:
            following_users = _get_following_users(
                viewer
            )

            if follow_filter == "following":
                if not following_users:
                    return ok(
                        "Sellers fetched successfully.",
                        data=_build_empty_response(
                            limit=limit,
                            offset=offset,
                        ),
                    )

                conditions.append(
                    "s.user IN %s"
                )
                params.append(
                    tuple(following_users)
                )

            elif follow_filter == "not_following":
                if following_users:
                    conditions.append(
                        "s.user NOT IN %s"
                    )
                    params.append(
                        tuple(following_users)
                    )

        where_clause = " AND ".join(conditions)

        sellers = frappe.db.sql(
            f"""
            SELECT
                s.name,
                s.user,
                s.business_category,
                s.business_address,
                s.rating,
                s.total_reviews,
                s.total_ads,
                s.seller_type,
                s.creation,

                COALESCE(
                    s.chat_response_time_seconds,
                    0
                ) AS chat_response_time_seconds,

                COALESCE(
                    s.chat_response_rate,
                    0
                ) AS chat_response_rate,

                COALESCE(
                    s.chat_response_sample_size,
                    0
                ) AS chat_response_sample_size,

                COALESCE(
                    s.chat_response_requests,
                    0
                ) AS chat_response_requests,

                s.response_metrics_updated_at,

                u.full_name,
                u.user_image,

                COALESCE(
                    p.total_followers,
                    0
                ) AS total_followers,

                COALESCE(
                    p.total_following,
                    0
                ) AS total_following,

                COALESCE(
                    p.is_verified,
                    0
                ) AS is_verified,

                (
                    SELECT COUNT(*)

                    FROM `tabAOS Follow` outgoing_follow

                    INNER JOIN `tabAOS Follow` incoming_follow
                        ON incoming_follow.follower_user
                            = outgoing_follow.following_user
                        AND incoming_follow.following_user
                            = outgoing_follow.follower_user

                    WHERE outgoing_follow.follower_user = s.user
                ) AS total_friends

            FROM `tabAOS Seller` s

            INNER JOIN `tabUser` u
                ON u.name = s.user

            INNER JOIN `tabAOS Profile` p
                ON p.user = s.user

            WHERE {where_clause}

            ORDER BY
                COALESCE(
                    p.is_verified,
                    0
                ) DESC,
                COALESCE(
                    s.rating,
                    0
                ) DESC,
                COALESCE(
                    p.total_followers,
                    0
                ) DESC,
                s.creation DESC

            LIMIT %s OFFSET %s
            """,
            (
                *params,
                limit,
                offset,
            ),
            as_dict=True,
        )

        items = [
            _serialize_seller(
                seller=seller,
                viewer=viewer,
                is_logged_in=is_logged_in,
            )
            for seller in sellers
        ]

        return ok(
            "Sellers fetched successfully.",
            data={
                "items": items,
                "limit": limit,
                "offset": offset,
                "count": len(items),
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Sellers Failed",
        )

        return fail(
            "Failed to fetch sellers.",
            code="INTERNAL_ERROR",
        )


def _serialize_seller(
    *,
    seller: dict,
    viewer: str | None,
    is_logged_in: bool,
) -> dict:
    """Serialize a seller discovery item."""

    seller_user = seller.get("user")

    rating = to_float(
        seller.get("rating")
    )

    total_reviews = to_non_negative_int(
        seller.get("total_reviews")
    )
    total_followers = to_non_negative_int(
        seller.get("total_followers")
    )
    total_following = to_non_negative_int(
        seller.get("total_following")
    )
    total_friends = to_non_negative_int(
        seller.get("total_friends")
    )
    total_ads = to_non_negative_int(
        seller.get("total_ads")
    )

    response_time_seconds = to_non_negative_int(
        seller.get(
            "chat_response_time_seconds"
        )
    )
    response_rate = _clamp_percentage(
        seller.get(
            "chat_response_rate"
        )
    )
    response_sample_size = to_non_negative_int(
        seller.get(
            "chat_response_sample_size"
        )
    )
    response_requests = to_non_negative_int(
        seller.get(
            "chat_response_requests"
        )
    )

    item = {
        "seller": seller.get("name"),
        "user": seller_user,
        "display_name": (
            seller.get("full_name")
            or seller_user
        ),
        "avatar": seller.get("user_image"),
        "business_category": seller.get(
            "business_category"
        ),
        "business_address": seller.get(
            "business_address"
        ),
        "is_verified": bool(
            seller.get("is_verified")
        ),
        "seller_type": seller.get(
            "seller_type"
        ),

        # Seller rating.
        "rating": rating,
        "rating_display": format_rating(
            rating
        ),

        # Seller reviews.
        "total_reviews": total_reviews,
        "total_reviews_display": humanize_count(
            total_reviews
        ),

        # Social metrics.
        "total_followers": total_followers,
        "total_followers_display": humanize_count(
            total_followers
        ),
        "total_following": total_following,
        "total_following_display": humanize_count(
            total_following
        ),
        "total_friends": total_friends,
        "total_friends_display": humanize_count(
            total_friends
        ),

        # Marketplace metrics.
        "total_ads": total_ads,
        "total_ads_display": humanize_count(
            total_ads
        ),

        # Chat response metrics.
        #
        # Keep the exact values for application logic while also returning
        # presentation-ready labels for the frontend.
        "response_time_seconds": (
            response_time_seconds
            if response_sample_size > 0
            else None
        ),
        "response_time_display": format_response_time(
            response_time_seconds,
            sample_size=response_sample_size,
        ),
        "response_rate": round(
            response_rate,
            2,
        ),
        "response_rate_display": format_response_rate(
            response_rate,
            response_requests=response_requests,
        ),
        "response_sample_size": response_sample_size,
        "response_requests": response_requests,
        "response_metrics_updated_at": seller.get(
            "response_metrics_updated_at"
        ),
    }

    item.update(
        _build_seller_relationship_payload(
            current_user_value=viewer,
            target_user=seller_user,
            is_logged_in=is_logged_in,
        )
    )

    return item


def _build_seller_relationship_payload(
    *,
    current_user_value: str | None,
    target_user: str,
    is_logged_in: bool,
) -> dict:
    """
    Build viewer-specific relationship fields for a seller user.

    Follow is user-to-user:
        current_user follows seller.user
    """

    if not is_logged_in:
        return _guest_relationship_payload(
            target_user=target_user,
        )

    return build_relationship_status(
        current_user=current_user_value,
        target_user=target_user,
    )


def _guest_relationship_payload(
    *,
    target_user: str,
) -> dict:
    """Return default relationship fields for a guest viewer."""

    return {
        "target_user": target_user,
        "is_self": False,
        "is_following": False,
        "is_followed_by": False,
        "is_friend": False,
        "relationship_status": "none",
        "action_label": "Follow",
    }


def _get_following_users(
    user: str,
) -> set[str]:
    """Return all users currently followed by the supplied user."""

    follows = frappe.get_all(
        "AOS Follow",
        filters={
            "follower_user": user,
        },
        fields=[
            "following_user",
        ],
    )

    return {
        row["following_user"]
        for row in follows
        if row.get("following_user")
    }


def _validate_follow_filter(
    *,
    follow_filter: str | None,
    is_logged_in: bool,
):
    """Validate the optional seller follow filter."""

    if not follow_filter:
        return None

    if follow_filter not in VALID_FOLLOW_FILTERS:
        return fail(
            "follow_filter must be following or not_following.",
            code="VALIDATION_ERROR",
        )

    if not is_logged_in:
        return fail(
            "Login is required to filter sellers by follow status.",
            code="AUTH_REQUIRED",
        )

    return None


def _get_verified_filter(
    value: Any,
):
    """
    Parse the optional is_verified filter.

    Returns:
        tuple[int | None, response | None]
    """

    if value is None or value == "":
        return None, None

    parsed = _parse_boolean_filter(value)

    if parsed is None:
        return (
            None,
            fail(
                "is_verified must be true or false.",
                code="VALIDATION_ERROR",
            ),
        )

    return parsed, None


def _parse_boolean_filter(
    value: Any,
) -> int | None:
    """Parse common boolean request values."""

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        if value in {
            0,
            1,
        }:
            return value

        return None

    normalized = str(value).strip().lower()

    if normalized in {
        "1",
        "true",
        "yes",
    }:
        return 1

    if normalized in {
        "0",
        "false",
        "no",
    }:
        return 0

    return None


def _normalize_optional_string(
    value: Any,
) -> str | None:
    """Trim an optional string and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None


def _clamp_percentage(
    value: Any,
) -> float:
    """Normalize a percentage into the inclusive range 0–100."""

    percentage = to_float(value)

    return max(
        0.0,
        min(percentage, 100.0),
    )


def _build_empty_response(
    *,
    limit: int,
    offset: int,
) -> dict:
    """Build the standard empty pagination response."""

    return {
        "items": [],
        "limit": limit,
        "offset": offset,
        "count": 0,
    }


def _get_limit(
    kwargs,
) -> int:
    """Return a safe seller discovery page size."""

    try:
        limit = int(
            kwargs.get("limit")
            or DEFAULT_LIMIT
        )
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    if limit <= 0:
        return DEFAULT_LIMIT

    return min(
        limit,
        MAX_LIMIT,
    )


def _get_offset(
    kwargs,
) -> int:
    """Return a safe non-negative pagination offset."""

    try:
        offset = int(
            kwargs.get("offset")
            or 0
        )
    except (TypeError, ValueError):
        offset = 0

    return max(
        offset,
        0,
    )
