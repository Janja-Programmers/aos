"""
Get Seller.

Used in:
- Seller Storefront
- Ad detail seller card
"""

from __future__ import annotations

import frappe
from frappe.utils import formatdate

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import GET_SELLER_LIMIT_PER_MINUTE_PER_IP


def get_seller_impl(**kwargs):
    """Fetch seller."""

    seller = kwargs.get("seller")

    if not seller:
        return fail("Seller is required.", code="VALIDATION_ERROR")

    rl = rate_limit(
        key="aos:sellers:get_seller:ip",
        ttl_seconds=60,
        limit=GET_SELLER_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        if not frappe.db.exists("AOS Seller", seller):
            return fail("Seller not found.", code="NOT_FOUND")

        seller_doc = frappe.get_doc("AOS Seller", seller)

        if seller_doc.status != "Active":
            return fail("Seller not available.", code="NOT_FOUND")

        user_doc = frappe.get_doc("User", seller_doc.user)

        profile = frappe.db.get_value(
            "AOS Profile",
            seller_doc.user,
            ["total_followers", "is_verified"],
            as_dict=True,
        )

        joined = formatdate(seller_doc.creation, "MMM yyyy")

        user = current_user()
        is_following = False

        if user and user != "Guest":
            is_following = frappe.db.exists(
                "AOS Follow",
                {
                    "following_user": seller_doc.user,
                    "follower_user": user,
                },
            )

        operating_hours = []

        if seller_doc.operating_hours:
            for row in seller_doc.operating_hours:
                operating_hours.append(
                    {
                        "day_of_week": row.day_of_week,
                        "is_open": bool(row.is_open),
                        "open_time": row.open_time,
                        "close_time": row.close_time,
                    }
                )

        return ok(
            "Seller fetched.",
            data={
                "seller": seller_doc.name,
                "user": seller_doc.user,
                "display_name": user_doc.full_name or seller_doc.user,
                "avatar": user_doc.user_image,
                "business_category": seller_doc.business_category,
                "seller_type": seller_doc.seller_type,
                "shop_banner": seller_doc.shop_banner,
                "about_business": seller_doc.about_business,
                "business_address": seller_doc.business_address,
                "is_verified": bool(profile.is_verified) if profile else False,
                "rating": seller_doc.rating,
                "total_reviews": seller_doc.total_reviews,
                "total_followers": profile.total_followers if profile else 0,
                "total_ads": seller_doc.total_ads,
                "joined": joined,
                "is_following": bool(is_following),
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
