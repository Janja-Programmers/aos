from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.market_context import resolve_market_country
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from .constants import WISHLIST_LIMIT_PER_MINUTE_PER_IP


def toggle_wishlist_impl(ad_id):
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

    ad_id = str(ad_id or "").strip()
    if not ad_id:
        return fail("Ad id is required.", code="VALIDATION_ERROR")

    # Enforce market isolation
    country, error = resolve_market_country(None)
    if error:
        return error

    ad_country = frappe.db.get_value(
        "AOS Ad",
        ad_id,
        "country"
    )

    if not ad_country:
        return fail("Ad not found.", code="NOT_FOUND")

    if ad_country != country:
        return fail(
            "You cannot wishlist an ad from another market.",
            code="MARKET_MISMATCH"
        )

    docname = f"{user}-{ad_id}"

    # Exists → toggle status
    if frappe.db.exists("AOS Wishlist", docname):
        doc = frappe.get_doc("AOS Wishlist", docname)
        doc.status = "Removed" if doc.status == "Active" else "Active"
        doc.save(ignore_permissions=True)

        return ok(
            "Wishlist updated.",
            data={"wishlisted": doc.status == "Active"},
        )

    # Create new
    doc = frappe.get_doc({
        "doctype": "AOS Wishlist",
        "user": user,
        "ad": ad_id,
        "status": "Active",
    })
    doc.insert(ignore_permissions=True)

    return ok(
        "Wishlist updated.",
        data={"wishlisted": True},
    )
