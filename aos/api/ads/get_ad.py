"""
Get a single Ad by id.
Country isolation and seller enforcement are strictly enforced.
Pricing operates in display currency.
"""

from __future__ import annotations

from typing import Any, Dict

import frappe
from frappe.utils import nowdate, getdate

from aos.api.shared.auth import current_user
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.utils import get_active_wishlist_ad_ids
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from .constants import GET_AD_LIMIT_PER_HOUR_PER_IP
from .serializers import serialize_ad_detail


def get_ad_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:ads:get:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=GET_AD_LIMIT_PER_HOUR_PER_IP,
        message="Too many requests. Please try again later.",
    )

    if rl:
        return rl

    ad_id = str(kwargs.get("ad_id") or "").strip()

    if not ad_id:
        return fail("Ad id is required.", code="VALIDATION_ERROR")

    # Market Context
    country, display_currency, error = resolve_market_context(
        country=kwargs.get("country"),
        currency=kwargs.get("currency"),
    )
    if error:
        return error

    user = current_user()
    today = getdate(nowdate())

    # Offer Logic
    offer_active_sql = """
        a.offer_price IS NOT NULL
        AND a.offer_price > 0
        AND (a.offer_start_date IS NULL OR a.offer_start_date <= %(today)s)
        AND (a.offer_end_date IS NULL OR a.offer_end_date >= %(today)s)
    """

    conversion_ratio = """
        (
            IFNULL(er_target.rate_vs_base, 1)
            /
            IFNULL(er_source.rate_vs_base, 1)
        )
    """

    original_price_sql = f"(a.price * {conversion_ratio})"

    current_price_sql = f"""
        CASE
            WHEN {offer_active_sql}
            THEN (a.offer_price * {conversion_ratio})
            ELSE (a.price * {conversion_ratio})
        END
    """

    # SQL Query
    sql = f"""
        SELECT
            a.*,
            %(display_currency)s as display_currency,
            {original_price_sql} as original_price_converted,
            {current_price_sql} as current_price
        FROM `tabAOS Ad` a
        INNER JOIN `tabAOS Seller` s ON s.name = a.seller
        LEFT JOIN `tabAOS Exchange Rate` er_source
            ON er_source.currency = a.currency
        LEFT JOIN `tabAOS Exchange Rate` er_target
            ON er_target.currency = %(display_currency)s
        WHERE a.name = %(ad_id)s
          AND a.status = 'Active'
          AND a.country = %(country)s
          AND s.status = 'Active'
          AND (a.expires_on IS NULL OR a.expires_on >= %(today)s)
        LIMIT 1
    """

    try:
        rows = frappe.db.sql(
            sql,
            {
                "ad_id": ad_id,
                "today": today,
                "country": country,
                "display_currency": display_currency,
            },
            as_dict=True,
        )

        if not rows:
            return fail("Ad not found.", code="NOT_FOUND")

        row = rows[0]

        doc = frappe._dict(row)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Ad FX Failed",
        )

        return fail(
            "Failed to fetch ad.",
            code="INTERNAL_ERROR",
        )

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
        "AOS Ad Attribute Value",
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

    # Offer flag
    doc.is_offer_active = bool(
        row.get("offer_price")
        and (
            row.get("offer_start_date") is None
            or row.get("offer_start_date") <= today
        )
        and (
            row.get("offer_end_date") is None
            or row.get("offer_end_date") >= today
        )
    )

    # Wishlist
    wishlisted_ids = set()

    if user != "Guest":
        wishlisted_ids = get_active_wishlist_ad_ids(user)

    item = serialize_ad_detail(
        doc,
        is_wishlisted=ad_id in wishlisted_ids,
    )

    return ok(
        "Ad fetched.",
        data={"item": item},
    )
