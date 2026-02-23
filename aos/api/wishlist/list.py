from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import nowdate, getdate

from aos.api.shared.auth import require_login
from aos.api.shared.market_context import (
    resolve_market_country,
    resolve_market_currency,
)
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from aos.api.ads.serializers import serialize_ad_list_item
from .constants import WISHLIST_LIMIT_PER_MINUTE_PER_IP

def list_wishlist_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:wishlist:list:ip:{request_ip()}",
        ttl_seconds=60,
        limit=WISHLIST_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    user, err = require_login()
    if err:
        return err

    # Market Context
    country, error = resolve_market_country(None)
    if error:
        return error

    display_currency, error = resolve_market_currency(kwargs.get("currency"))
    if error:
        return error

    today = getdate(nowdate())

    try:
        limit = int(kwargs.get("limit", 20))
        offset = int(kwargs.get("offset", 0))
    except Exception:
        return fail("Invalid pagination values.", code="VALIDATION_ERROR")

    limit = max(1, min(limit, 50))
    offset = max(0, offset)

    # Fetch Wishlist Ad IDs
    wishlist_rows = frappe.get_all(
        "AOS Wishlist",
        filters={"user": user, "status": "Active"},
        fields=["ad"],
        order_by="creation desc",
        start=offset,
        page_length=limit,
    )

    ad_ids = [r["ad"] for r in wishlist_rows]
    if not ad_ids:
        return ok(
            "Wishlist fetched.",
            data={
                "items": [],
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "returned": 0,
                },
            },
        )

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

    # Final SQL
    sql = f"""
        SELECT
            a.name,
            a.title,
            a.status,
            a.country,
            a.location,
            a.category,
            a.currency,
            %(display_currency)s as display_currency,
            a.price_type,
            a.price_unit,
            a.offer_price,
            a.offer_start_date,
            a.offer_end_date,
            a.offer_percent,
            a.average_rating,
            a.total_reviews,
            a.creation,
            {original_price_sql} as original_price_converted,
            {current_price_sql} as current_price
        FROM `tabAOS Ad` a
        INNER JOIN `tabAOS Seller` s ON s.name = a.user
        LEFT JOIN `tabAOS Exchange Rate` er_source
            ON er_source.currency = a.currency
        LEFT JOIN `tabAOS Exchange Rate` er_target
            ON er_target.currency = %(display_currency)s
        WHERE a.name IN %(ad_ids)s
          AND a.status = 'Active'
          AND a.country = %(country)s
          AND s.status = 'Active'
        ORDER BY a.creation desc
    """

    try:
        ads = frappe.db.sql(
            sql,
            {
                "ad_ids": tuple(ad_ids),
                "today": today,
                "country": country,
                "display_currency": display_currency,
            },
            as_dict=True,
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Wishlist FX Failed")
        return fail("Failed to fetch wishlist.", code="INTERNAL_ERROR")

    # Fetch Images
    ad_names = [r["name"] for r in ads]
    images_by_ad: Dict[str, List[Dict[str, Any]]] = {n: [] for n in ad_names}

    if ad_names:
        img_rows = frappe.get_all(
            "AOS Ad Image",
            filters={"parenttype": "AOS Ad", "parent": ["in", ad_names]},
            fields=["parent", "image", "is_primary", "sort_order"],
        )

        for img in img_rows:
            images_by_ad.setdefault(img["parent"], []).append(img)

    # Build Response
    items = []

    for row in ads:
        ad_doc = frappe._dict(row)
        ad_doc.images = images_by_ad.get(row["name"], [])
        ad_doc.is_offer_active = bool(
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

        items.append(
            serialize_ad_list_item(
                ad_doc,
                is_wishlisted=True,
            )
        )

    return ok(
        "Wishlist fetched.",
        data={
            "items": items,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(items),
            },
        },
    )
