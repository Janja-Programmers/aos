"""
List Sellers.

Marketplace discovery endpoint for browsing sellers.
Lightweight version of seller profile.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.social.relationship import build_relationship_status

from .constants import LIST_SELLERS_LIMIT_PER_MINUTE_PER_IP


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

        search = kwargs.get("search")
        is_verified = kwargs.get("is_verified")
        seller_type = kwargs.get("seller_type")
        business_category = kwargs.get("business_category") or kwargs.get("category")
        follow_filter = kwargs.get("follow_filter")

        viewer = current_user()
        is_logged_in = bool(viewer and viewer != "Guest")

        conditions = ["s.status = 'Active'"]
        params = []

        # Do not show the current user's own seller profile in discovery.
        if is_logged_in:
            conditions.append("s.user != %s")
            params.append(viewer)

        if is_verified is not None:
            conditions.append("p.is_verified = %s")
            params.append(int(is_verified))

        if seller_type:
            conditions.append("s.seller_type = %s")
            params.append(seller_type)

        if business_category:
            conditions.append("s.business_category = %s")
            params.append(business_category)

        if search:
            conditions.append(
                """
                (
                    u.full_name LIKE %s
                    OR s.business_category LIKE %s
                    OR s.business_address LIKE %s
                )
                """
            )
            search_value = f"%{search}%"
            params.extend([search_value, search_value, search_value])

        following_users = set()

        if is_logged_in:
            follows = frappe.get_all(
                "AOS Follow",
                filters={"follower_user": viewer},
                fields=["following_user"],
            )
            following_users = {f["following_user"] for f in follows}

            if follow_filter == "following":
                if not following_users:
                    return ok(
                        "Sellers fetched successfully.",
                        data={
                            "items": [],
                            "limit": limit,
                            "offset": offset,
                            "count": 0,
                        },
                    )

                conditions.append("s.user IN %s")
                params.append(tuple(following_users))

            elif follow_filter == "not_following":
                if following_users:
                    conditions.append("s.user NOT IN %s")
                    params.append(tuple(following_users))

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
                s.seller_type,

                u.full_name,
                u.user_image,

                COALESCE(p.total_followers, 0) AS total_followers,
                COALESCE(p.total_following, 0) AS total_following,
                COALESCE(p.is_verified, 0) AS is_verified

            FROM `tabAOS Seller` s
            INNER JOIN `tabUser` u
                ON u.name = s.user
            INNER JOIN `tabAOS Profile` p
                ON p.user = s.user

            WHERE {where_clause}

            ORDER BY
                COALESCE(p.is_verified, 0) DESC,
                s.rating DESC,
                COALESCE(p.total_followers, 0) DESC,
                s.creation DESC

            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
            as_dict=True,
        )

        items = []

        for seller in sellers:
            seller_user = seller.get("user")

            item = {
                "seller": seller.get("name"),
                "user": seller_user,
                "display_name": seller.get("full_name") or seller_user,
                "avatar": seller.get("user_image"),
                "business_category": seller.get("business_category"),
                "business_address": seller.get("business_address"),
                "is_verified": bool(seller.get("is_verified")),
                "seller_type": seller.get("seller_type"),
                "rating": seller.get("rating"),
                "total_reviews": seller.get("total_reviews") or 0,
                "total_followers": int(seller.get("total_followers") or 0),
                "total_following": int(seller.get("total_following") or 0),
            }

            item.update(
                _build_seller_relationship_payload(
                    current_user_value=viewer,
                    target_user=seller_user,
                    is_logged_in=is_logged_in,
                )
            )

            items.append(item)

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
        return _guest_relationship_payload(target_user=target_user)

    return build_relationship_status(
        current_user=current_user_value,
        target_user=target_user,
    )


def _guest_relationship_payload(*, target_user: str) -> dict:
    return {
        "target_user": target_user,
        "is_self": False,
        "is_following": False,
        "is_followed_by": False,
        "is_friend": False,
        "relationship_status": "none",
        "action_label": "Follow",
    }


def _get_limit(kwargs) -> int:
    try:
        limit = int(kwargs.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20

    if limit <= 0:
        return 20

    return min(limit, 50)


def _get_offset(kwargs) -> int:
    try:
        offset = int(kwargs.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0

    return max(offset, 0)
