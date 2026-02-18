"""
Get Seller Profile.

Used in:
- Seller Storefront
- Ad detail seller card
"""

from __future__ import annotations

import frappe
from frappe.utils import formatdate

from aos.api.shared.auth import current_user
from aos.api.shared.responses import fail, ok


def get_seller_impl(**kwargs):
    """Fetch seller profile."""

    seller = kwargs.get("seller")

    if not seller:
        return fail("Seller is required.", code="VALIDATION_ERROR")

    try:
        if not frappe.db.exists("AOS Seller", seller):
            return fail("Seller not found.", code="NOT_FOUND")

        seller_doc = frappe.get_doc("AOS Seller", seller)

        if seller_doc.status != "Active":
            return fail("Seller not available.", code="NOT_FOUND")

        avatar = seller_doc.avatar or frappe.db.get_value(
            "User", seller, "user_image"
        )

        joined = formatdate(seller_doc.creation, "MMM yyyy")

        # Determine follow state
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

        return ok(
            "Seller profile fetched.",
            data={
                "shop_name": seller_doc.shop_name,
                "avatar": avatar,
                "shop_banner": seller_doc.shop_banner,
                "rating": seller_doc.rating,
                "total_reviews": seller_doc.total_reviews,
                "total_followers": seller_doc.total_followers,
                "total_ads": seller_doc.total_ads,
                "joined": joined,
                "is_following": bool(is_following)
            }
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Seller Profile Failed"
        )
        return fail("Failed to fetch seller profile.", code="INTERNAL_ERROR")
