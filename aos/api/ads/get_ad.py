"""Get a single Ad by id."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import nowdate, getdate

from aos.api.shared.auth import current_user
from aos.api.shared.utils import get_active_wishlist_ad_ids
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from .constants import GET_AD_LIMIT_PER_HOUR_PER_IP
from .serializers import serialize_ad_detail


def get_ad_impl(ad_id: Any):
    rl = rate_limit(
        key=f"aos:ads:get:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=GET_AD_LIMIT_PER_HOUR_PER_IP,
        message="Too many requests. Please try again later.",
    )
    if rl:
        return rl

    ad_id = str(ad_id or "").strip()
    if not ad_id:
        return fail("Ad id is required.", code="VALIDATION_ERROR")

    today = getdate(nowdate())
    offer_active_sql = """
        offer_price IS NOT NULL
        AND offer_price > 0
        AND (offer_start_date IS NULL OR offer_start_date <= %(today)s)
        AND (offer_end_date IS NULL OR offer_end_date >= %(today)s)
    """

    current_price_sql = f"""
        CASE
            WHEN {offer_active_sql}
            THEN offer_price
            ELSE price
        END
    """

    sql = f"""
        SELECT
            *,
            {current_price_sql} as current_price
        FROM `tabAOS Ad`
        WHERE name = %(ad_id)s
          AND status = 'Active'
        LIMIT 1
    """

    try:
        rows = frappe.db.sql(
            sql,
            {"ad_id": ad_id, "today": today},
            as_dict=True,
        )

        if not rows:
            return fail("Ad not found.", code="NOT_FOUND")

        row = rows[0]
        doc = frappe._dict(row)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Ad Failed")
        return fail("Failed to fetch ad.", code="INTERNAL_ERROR")

    # Load Child Tables
    doc.images = frappe.get_all(
        "AOS Ad Image",
        filters={"parent": ad_id, "parenttype": "AOS Ad"},
        fields=["image", "is_primary", "sort_order"],
    )

    doc.details = frappe.get_all(
        "Ad Attribute Value",
        filters={"parent": ad_id, "parenttype": "AOS Ad"},
        fields=[
            "attribute",
            "value_text",
            "value_number",
            "value_date",
            "value_bool",
            "value_json",
        ],
    )

    # Offer flag
    doc.is_offer_active = bool(
        row.get("offer_price")
        and (row.get("offer_start_date") is None or row.get("offer_start_date") <= today)
        and (row.get("offer_end_date") is None or row.get("offer_end_date") >= today)
    )

    # Wishlist
    user = current_user()
    wishlisted_ids = get_active_wishlist_ad_ids(user)

    item = serialize_ad_detail(
        doc,
        is_wishlisted=ad_id in wishlisted_ids,
    )

    return ok("Ad fetched.", data={"item": item})
