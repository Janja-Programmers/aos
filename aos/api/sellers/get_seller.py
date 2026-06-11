"""
Get Seller.

Used in:
- Seller Storefront
- Ad detail seller card

Returns:
- Seller profile information
- Social metrics
- Rating and review metrics
- Chat response metrics
- Public seller storefront location
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import formatdate

from aos.api.shared.auth import current_user
from aos.api.shared.formatters import (
    format_rating,
    humanize_count,
    to_float,
    to_non_negative_int,
)
from aos.api.shared.rate_limit import (
    rate_limit,
    request_ip,
)
from aos.api.shared.responses import fail, ok
from aos.api.social.relationship import build_relationship_status
from aos.services.seller_response_metrics import (
    format_response_rate,
    format_response_time,
)

from .constants import GET_SELLER_LIMIT_PER_MINUTE_PER_IP
from .serializers import serialize_seller_location


def get_seller_impl(**kwargs):
    """Fetch an active seller."""

    seller = _normalize_optional_string(
        kwargs.get("seller")
    )

    if not seller:
        return fail(
            "Seller is required.",
            code="VALIDATION_ERROR",
        )

    ip = request_ip()

    rl = rate_limit(
        key=f"aos:sellers:get_seller:ip:{ip}",
        ttl_seconds=60,
        limit=GET_SELLER_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        if not frappe.db.exists(
            "AOS Seller",
            seller,
        ):
            return fail(
                "Seller not found.",
                code="NOT_FOUND",
            )

        seller_doc = frappe.get_doc(
            "AOS Seller",
            seller,
        )

        if seller_doc.status != "Active":
            return fail(
                "Seller not available.",
                code="NOT_FOUND",
            )

        user_doc = frappe.get_doc(
            "User",
            seller_doc.user,
        )

        profile = frappe.db.get_value(
            "AOS Profile",
            {
                "user": seller_doc.user,
            },
            [
                "total_followers",
                "total_following",
                "is_verified",
            ],
            as_dict=True,
        )

        viewer = current_user()

        is_logged_in = bool(
            viewer
            and viewer != "Guest"
        )

        can_edit = (
            is_logged_in
            and viewer == seller_doc.user
        )

        relationship = (
            _build_seller_relationship_payload(
                current_user_value=viewer,
                target_user=seller_doc.user,
                is_logged_in=is_logged_in,
            )
        )

        rating = to_float(
            seller_doc.rating
        )

        total_reviews = to_non_negative_int(
            seller_doc.total_reviews
        )

        total_followers = to_non_negative_int(
            profile.get("total_followers")
            if profile
            else 0
        )

        total_following = to_non_negative_int(
            profile.get("total_following")
            if profile
            else 0
        )

        total_friends = _get_total_friends(
            seller_doc.user
        )

        total_ads = to_non_negative_int(
            seller_doc.total_ads
        )

        response_time_seconds = to_non_negative_int(
            seller_doc.chat_response_time_seconds
        )

        response_rate = _clamp_percentage(
            seller_doc.chat_response_rate
        )

        response_sample_size = to_non_negative_int(
            seller_doc.chat_response_sample_size
        )

        response_requests = to_non_negative_int(
            seller_doc.chat_response_requests
        )

        joined = formatdate(
            seller_doc.creation,
            "MMM yyyy",
        )

        operating_hours = _serialize_operating_hours(
            seller_doc.operating_hours
        )

        return ok(
            "Seller fetched.",
            data={
                "seller": seller_doc.name,
                "user": seller_doc.user,
                "display_name": (
                    user_doc.full_name
                    or seller_doc.user
                ),
                "avatar": user_doc.user_image,
                "business_category": (
                    seller_doc.business_category
                ),
                "seller_type": seller_doc.seller_type,
                "shop_banner": seller_doc.shop_banner,
                "about_business": (
                    seller_doc.about_business
                ),
                "is_verified": bool(
                    profile.get("is_verified")
                    if profile
                    else False
                ),

                # Public storefront location.
                "location": serialize_seller_location(
                    seller_doc
                ),

                # Rating
                "rating": rating,
                "rating_display": format_rating(
                    rating
                ),

                # Reviews
                "total_reviews": total_reviews,
                "total_reviews_display": humanize_count(
                    total_reviews
                ),

                # Followers
                "total_followers": total_followers,
                "total_followers_display": humanize_count(
                    total_followers
                ),

                # Following
                "total_following": total_following,
                "total_following_display": humanize_count(
                    total_following
                ),

                # Friends are mutual follows.
                "total_friends": total_friends,
                "total_friends_display": humanize_count(
                    total_friends
                ),

                # Ads
                "total_ads": total_ads,
                "total_ads_display": humanize_count(
                    total_ads
                ),

                # Chat response metrics.
                #
                # response_time_seconds is null until at least one
                # successful seller response exists.
                #
                # The public display value remains null until at least
                # three successful response samples exist.
                "response_time_seconds": (
                    response_time_seconds
                    if response_sample_size > 0
                    else None
                ),
                "response_time_display": format_response_time(
                    response_time_seconds,
                    sample_size=response_sample_size,
                ),

                # Response-rate display remains null until at least three
                # incoming response requests have been observed.
                "response_rate": round(
                    response_rate,
                    2,
                ),
                "response_rate_display": format_response_rate(
                    response_rate,
                    response_requests=response_requests,
                ),
                "response_sample_size": (
                    response_sample_size
                ),
                "response_requests": response_requests,
                "response_metrics_updated_at": (
                    seller_doc.response_metrics_updated_at
                ),

                "joined": joined,
                "can_edit": can_edit,

                **relationship,

                "operating_hours": operating_hours,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Seller Failed",
        )

        return fail(
            "Failed to fetch seller.",
            code="INTERNAL_ERROR",
        )


def _serialize_operating_hours(
    rows,
) -> list[dict]:
    """Serialize the seller's weekly operating hours."""

    if not rows:
        return []

    return [
        {
            "day_of_week": row.day_of_week,
            "is_open": bool(row.is_open),
            "open_time": row.open_time,
            "close_time": row.close_time,
        }
        for row in rows
    ]


def _get_total_friends(
    user: str,
) -> int:
    """
    Return the number of users who mutually follow the supplied user.

    A friendship exists when:
    - the seller follows another user; and
    - that user follows the seller back.
    """

    result = frappe.db.sql(
        """
        SELECT COUNT(*) AS total_friends

        FROM `tabAOS Follow` outgoing_follow

        INNER JOIN `tabAOS Follow` incoming_follow
            ON incoming_follow.follower_user
                = outgoing_follow.following_user
            AND incoming_follow.following_user
                = outgoing_follow.follower_user

        WHERE outgoing_follow.follower_user = %s
        """,
        (user,),
        as_dict=True,
    )

    if not result:
        return 0

    return to_non_negative_int(
        result[0].get("total_friends")
    )


def _build_seller_relationship_payload(
    *,
    current_user_value: str | None,
    target_user: str,
    is_logged_in: bool,
) -> dict:
    """
    Build viewer-specific relationship fields for a seller user.

    Following a seller is user-to-user:
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
        min(
            percentage,
            100.0,
        ),
    )
