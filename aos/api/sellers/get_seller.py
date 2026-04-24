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

        avatar = seller_doc.avatar or frappe.db.get_value(
            "User",
            seller_doc.user,
            "user_image"
        )

        joined = formatdate(seller_doc.creation, "MMM yyyy")

        # FOLLOW STATE
        user = current_user()
        is_following = False

        if user and user != "Guest":
            is_following = frappe.db.exists(
                "AOS Seller Follow",
                {
                    "seller": seller,
                    "follower": user
                }
            )

        # OPERATING HOURS
        operating_hours = []

        if seller_doc.operating_hours:
            for row in seller_doc.operating_hours:
                operating_hours.append({
                    "day_of_week": row.day_of_week,
                    "is_open": bool(row.is_open),
                    "open_time": row.open_time,
                    "close_time": row.close_time
                })

        return ok(
            "Seller fetched.",
            data={
                "seller": seller_doc.name,
                "user": seller_doc.user,
                "shop_name": seller_doc.shop_name,
                "category": seller_doc.category,
                "seller_type": seller_doc.seller_type,
                "avatar": avatar,
                "shop_banner": seller_doc.shop_banner,
                "about_shop": seller_doc.about_shop,
                "physical_address": seller_doc.physical_address,
                "is_verified": seller_doc.is_verified,
                "rating": seller_doc.rating,
                "total_reviews": seller_doc.total_reviews,
                "total_followers": seller_doc.total_followers,
                "total_ads": seller_doc.total_ads,
                "joined": joined,
                "is_following": bool(is_following),
                "operating_hours": operating_hours
            }
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Seller Failed"
        )
        return fail(
            "Failed to fetch seller.",
            code="INTERNAL_ERROR"
        )
