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

        # INPUTS
        limit = int(kwargs.get("limit", 20))
        offset = int(kwargs.get("offset", 0))

        search = kwargs.get("search")
        is_verified = kwargs.get("is_verified")
        seller_type = kwargs.get("seller_type")
        category = kwargs.get("category")
        follow_filter = kwargs.get("follow_filter")

        user = current_user()

        # BASE FILTERS
        filters = {
            "status": "Active",
        }

        # Do not show logged-in seller their own seller profile in discovery.
        if user and user != "Guest":
            filters["user"] = ["!=", user]

        if is_verified is not None:
            filters["is_verified"] = int(is_verified)

        if seller_type:
            filters["seller_type"] = seller_type

        if category:
            filters["category"] = category

        # FOLLOW FILTER
        following_ids = set()

        if user and user != "Guest":
            follows = frappe.get_all(
                "AOS Seller Follow",
                filters={
                    "follower": user,
                },
                fields=["seller"],
            )
            following_ids = {f["seller"] for f in follows}

            if follow_filter == "following":
                if not following_ids:
                    return ok(
                        "Sellers fetched successfully.",
                        data={
                            "items": [],
                            "limit": limit,
                            "offset": offset,
                            "count": 0,
                        },
                    )

                filters["name"] = ["in", list(following_ids)]

            elif follow_filter == "not_following":
                if following_ids:
                    filters["name"] = ["not in", list(following_ids)]

        # SEARCH
        or_filters = None

        if search:
            or_filters = [
                ["shop_name", "like", f"%{search}%"],
            ]

        # FETCH SELLERS
        sellers = frappe.get_all(
            "AOS Seller",
            filters=filters,
            or_filters=or_filters,
            fields=[
                "name",
                "user",
                "shop_name",
                "category",
                "avatar",
                "physical_address",
                "rating",
                "total_reviews",
                "total_followers",
                "is_verified",
                "seller_type",
            ],
            limit=limit,
            start=offset,
            order_by=(
                "is_verified desc, "
                "rating desc, "
                "total_followers desc, "
                "creation desc"
            ),
        )

        # FORMAT RESPONSE
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
                    "total_followers": s.get("total_followers"),
                    "is_following": s["name"] in following_ids,
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
