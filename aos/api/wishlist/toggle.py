from __future__ import annotations

import frappe
from frappe.utils import nowdate, getdate

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from .constants import WISHLIST_LIMIT_PER_MINUTE_PER_IP


def toggle_wishlist_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:wishlist:toggle:ip:{request_ip()}",
        ttl_seconds=60,
        limit=WISHLIST_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    user, err = require_login()
    if err:
        return err

    ad_id = str(kwargs.get("ad_id") or "").strip()

    if not ad_id:
        return fail("Ad id is required.", code="VALIDATION_ERROR")

    today = getdate(nowdate())

    try:
        # Fetch Ad
        ad = frappe.db.get_value(
            "AOS Ad",
            ad_id,
            [
                "name",
                "status",
                "seller",
                "expires_on",
            ],
            as_dict=True,
        )

        if not ad:
            return fail("Ad not found.", code="NOT_FOUND")

        # Ad must be active
        if ad.status != "Active":
            return fail(
                "Only active ads can be added to wishlist.",
                code="INVALID_AD",
            )

        # Ad must not be expired
        if ad.expires_on and getdate(ad.expires_on) < today:
            return fail(
                "This ad has expired.",
                code="EXPIRED_AD",
            )

        # Seller must be active
        seller_status = frappe.db.get_value(
            "AOS Seller",
            ad.seller,
            "status",
        )

        if seller_status != "Active":
            return fail(
                "This seller is not available.",
                code="SELLER_INACTIVE",
            )

        docname = f"{user}-{ad_id}"

        # Toggle existing wishlist entry
        if frappe.db.exists("AOS Wishlist", docname):
            doc = frappe.get_doc("AOS Wishlist", docname)
            doc.status = "Removed" if doc.status == "Active" else "Active"
            doc.save(ignore_permissions=True)

            return ok(
                "Wishlist updated.",
                data={"wishlisted": doc.status == "Active"},
            )

        # Create new wishlist entry
        doc = frappe.new_doc("AOS Wishlist")
        doc.user = user
        doc.ad = ad_id
        doc.status = "Active"

        doc.insert(ignore_permissions=True)

        return ok(
            "Wishlist updated.",
            data={"wishlisted": True},
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Toggle Wishlist Failed",
        )

        return fail(
            "Failed to update wishlist.",
            code="INTERNAL_ERROR",
        )
