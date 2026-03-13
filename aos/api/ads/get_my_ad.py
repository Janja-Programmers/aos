"""
Get a single Ad owned by the current user.

Rules:
 - User must be authenticated
 - Ad must belong to the user's seller account
 - Any status is allowed
 - Raw stored pricing is returned (no currency conversion)
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.responses import fail, ok
from aos.api.shared.rate_limit import rate_limit

from .constants import GET_MY_AD_LIMIT_PER_MINUTE_PER_USER
from .serializers import serialize_ad_detail


def get_my_ad_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:ads:get_my:user:{user}",
        ttl_seconds=60,
        limit=GET_MY_AD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    ad_id = str(kwargs.get("ad_id") or "").strip()

    if not ad_id:
        return fail("Ad id is required.", code="VALIDATION_ERROR")

    # Resolve seller from logged-in user
    seller = frappe.db.get_value(
        "AOS Seller",
        {"user": user},
        "name",
    )

    if not seller:
        return fail("Seller profile not found.", code="FORBIDDEN")

    # Ensure ad belongs to seller
    row = frappe.db.get_value(
        "AOS Ad",
        ad_id,
        ["name", "seller"],
        as_dict=True,
    )

    if not row:
        return fail("Ad not found.", code="NOT_FOUND")

    if row.seller != seller:
        return fail("You do not have permission to view this ad.", code="FORBIDDEN")

    try:
        doc = frappe.get_doc("AOS Ad", ad_id)

        # Images
        doc.images = frappe.get_all(
            "AOS Ad Image",
            filters={
                "parent": ad_id,
                "parenttype": "AOS Ad",
            },
            fields=["image", "is_primary", "sort_order"],
            order_by="is_primary desc, sort_order asc",
        )

        # Details
        doc.details = frappe.get_all(
            "Ad Attribute Value",
            filters={
                "parent": ad_id,
                "parenttype": "AOS Ad",
            },
            fields=[
                "attribute",
                "value_text",
                "value_number",
                "value_date",
                "value_bool",
                "value_json",
            ],
        )

        item = serialize_ad_detail(doc)

        return ok(
            "Ad fetched.",
            data={"item": item},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get My Ad Failed")
        return fail("Failed to fetch ad.", code="INTERNAL_ERROR")
