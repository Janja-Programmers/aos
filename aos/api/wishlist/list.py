from __future__ import annotations

from typing import Any, Dict, Optional

import frappe
from frappe.utils import nowdate, getdate

from aos.api.shared.auth import require_login
from aos.api.shared.market_context import resolve_market_currency
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from aos.api.ads.serializers import serialize_ad_list_item
from .constants import WISHLIST_LIMIT_PER_MINUTE_PER_IP


ALLOWED_SORTS = {"rating_high", "price_low", "price_high", "recent"}
ALLOWED_PRICE_TYPES = {"Fixed", "Negotiable", "Contact for price", "Free"}
ALLOWED_PROMOTIONS = {"offer", "deal", "flash_sale"}


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        string_value = str(value).strip()
        if string_value == "":
            return None
        return float(string_value)
    except Exception:
        return None


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

    try:
        # Market Context
        display_currency, error = resolve_market_currency(kwargs.get("currency"))
        if error:
            return error

        today = getdate(nowdate())

        # Inputs
        sort = str(kwargs.get("sort") or "recent").strip()
        q = str(kwargs.get("q") or "").strip()
        category = str(kwargs.get("category") or "").strip()
        promotion_type = str(kwargs.get("promotion_type") or "").strip()
        price_type = str(kwargs.get("price_type") or "").strip()
        price_min = _safe_float(kwargs.get("price_min"))
        price_max = _safe_float(kwargs.get("price_max"))

        try:
            limit = max(1, min(int(kwargs.get("limit", 20)), 50))
        except Exception:
            limit = 20

        try:
            offset = max(0, int(kwargs.get("offset", 0)))
        except Exception:
            offset = 0

        # Validation
        if sort not in ALLOWED_SORTS:
            return fail("Invalid sort.", code="VALIDATION_ERROR")

        if price_type and price_type not in ALLOWED_PRICE_TYPES:
            return fail("Invalid price_type.", code="VALIDATION_ERROR")

        if promotion_type and promotion_type not in ALLOWED_PROMOTIONS:
            return fail("Invalid promotion_type.", code="VALIDATION_ERROR")

        if price_min is not None and price_max is not None and price_min > price_max:
            return fail(
                "price_min cannot be greater than price_max.",
                code="VALIDATION_ERROR",
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

        # Base Conditions
        conditions = [
            "w.user = %(user)s",
            "w.status = 'Active'",
            "a.status = 'Active'",
            "s.status = 'Active'",
            "(a.expires_on IS NULL OR a.expires_on >= %(today)s)",
        ]

        values: Dict[str, Any] = {
            "user": user,
            "display_currency": display_currency,
            "today": today,
        }

        # Search
        if q and len(q) >= 2:
            conditions.append("a.title LIKE %(q)s")
            values["q"] = f"%{q}%"

        # Category
        if category:
            conditions.append("a.category = %(category)s")
            values["category"] = category

        # Price Type
        if price_type:
            conditions.append("a.price_type = %(price_type)s")
            values["price_type"] = price_type

        # Promotions
        if promotion_type:
            conditions.append(f"({offer_active_sql})")

        # Price Filters
        if price_min is not None:
            conditions.append(f"{current_price_sql} >= %(price_min)s")
            values["price_min"] = price_min

        if price_max is not None:
            conditions.append(f"{current_price_sql} <= %(price_max)s")
            values["price_max"] = price_max

        where_clause = " AND ".join(conditions)

        # Sorting
        if sort == "price_low":
            order_by = f"{current_price_sql} asc, a.creation desc"
        elif sort == "price_high":
            order_by = f"{current_price_sql} desc, a.creation desc"
        elif sort == "rating_high":
            order_by = "a.average_rating desc, a.total_reviews desc, a.creation desc"
        else:
            order_by = "w.creation desc"

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
                a.price,
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
            FROM `tabAOS Wishlist` w
            INNER JOIN `tabAOS Ad` a ON a.name = w.ad
            INNER JOIN `tabAOS Seller` s ON s.name = a.seller
            LEFT JOIN `tabAOS Exchange Rate` er_source
                ON er_source.currency = a.currency
            LEFT JOIN `tabAOS Exchange Rate` er_target
                ON er_target.currency = %(display_currency)s
            WHERE {where_clause}
            ORDER BY {order_by}
            LIMIT %(limit)s OFFSET %(offset)s
        """

        values["limit"] = limit
        values["offset"] = offset

        ads = frappe.db.sql(sql, values, as_dict=True)

        # Fetch Images
        ad_names = [r["name"] for r in ads]
        images_by_ad = {n: [] for n in ad_names}

        if ad_names:
            img_rows = frappe.get_all(
                "AOS Ad Image",
                filters={"parenttype": "AOS Ad", "parent": ["in", ad_names]},
                fields=["parent", "image", "is_primary", "sort_order"],
                order_by="is_primary desc, sort_order asc",
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

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Wishlist List Failed",
        )

        return fail(
            "Failed to fetch wishlist.",
            code="INTERNAL_ERROR",
        )
