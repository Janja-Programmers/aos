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

        limit = int(kwargs.get("limit", 20))
        offset = int(kwargs.get("offset", 0))

        search = kwargs.get("search")
        is_verified = kwargs.get("is_verified")
        seller_type = kwargs.get("seller_type")
        category = kwargs.get("category")
        follow_filter = kwargs.get("follow_filter")

        user = current_user()

        conditions = ["s.status = 'Active'"]
        params = []

        if user and user != "Guest":
            conditions.append("s.user != %s")
            params.append(user)

        if is_verified is not None:
            conditions.append("s.is_verified = %s")
            params.append(int(is_verified))

        if seller_type:
            conditions.append("s.seller_type = %s")
            params.append(seller_type)

        if category:
            conditions.append("s.category = %s")
            params.append(category)

        if search:
            conditions.append("s.shop_name LIKE %s")
            params.append(f"%{search}%")

        following_users = set()

        if user and user != "Guest":
            follows = frappe.get_all(
                "AOS Follow",
                filters={"follower_user": user},
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
                s.shop_name,
                s.category,
                s.avatar,
                s.physical_address,
                s.rating,
                s.total_reviews,
                s.is_verified,
                s.seller_type,
                COALESCE(p.total_followers, 0) AS total_followers

            FROM `tabAOS Seller` s
            LEFT JOIN `tabAOS Profile` p ON p.user = s.user

            WHERE {where_clause}

            ORDER BY
                s.is_verified DESC,
                s.rating DESC,
                COALESCE(p.total_followers, 0) DESC,
                s.creation DESC

            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
            as_dict=True,
        )

        items = []

        for s in sellers:
            items.append(
                {
                    "seller": s["name"],
                    "user": s["user"],
                    "shop_name": s["shop_name"],
                    "category": s.get("category"),
                    "avatar": s.get("avatar"),
                    "physical_address": s.get("physical_address"),
                    "is_verified": s.get("is_verified"),
                    "seller_type": s.get("seller_type"),
                    "rating": s.get("rating"),
                    "total_reviews": s.get("total_reviews"),
                    "total_followers": s.get("total_followers") or 0,
                    "is_following": s["user"] in following_users,
                }
            )

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
