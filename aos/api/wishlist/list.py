from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from frappe.utils import nowdate, add_days, getdate

from aos.api.shared.auth import require_login
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.sql_safety import safe_like_contains
from aos.utils.aos_settings import get_aos_settings_snapshot
from aos.services.currency_conversion import sql_conversion_expressions

from aos.api.ads.category_filters import resolve_category_filter_values
from aos.api.ads.serializers import serialize_ad_list_item
from .constants import WISHLIST_LIMIT_PER_MINUTE_PER_IP


ALLOWED_SORTS = {"rating_high", "price_low", "price_high", "recent"}
ALLOWED_PRICE_TYPES = {"Fixed", "Negotiable", "Contact for price", "Free"}
ALLOWED_PROMOTIONS = {"offer", "deal", "flash_sale"}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


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

    sort = str(kwargs.get("sort") or "recent").strip() or "recent"
    if sort not in ALLOWED_SORTS:
        return fail(
            "Invalid sort.",
            error="VALIDATION_ERROR",
            data={"allowed": sorted(ALLOWED_SORTS)},
        )

    # Market Context
    country, display_currency, error = resolve_market_context(
        country=kwargs.get("country"),
        currency=kwargs.get("currency"),
    )
    if error:
        return error

    base_currency = get_aos_settings_snapshot().base_currency

    today = getdate(nowdate())

    # Inputs
    location = str(kwargs.get("location") or "").strip()
    category = str(kwargs.get("category") or "").strip()
    seller = str(kwargs.get("seller") or "").strip()
    q = str(kwargs.get("q") or "").strip()

    price_type = str(kwargs.get("price_type") or "").strip()
    promotion_type = str(kwargs.get("promotion_type") or "").strip()

    price_min = _safe_float(kwargs.get("price_min"))
    price_max = _safe_float(kwargs.get("price_max"))
    rating_min = _safe_float(kwargs.get("rating_min"))
    verified_seller = int(kwargs.get("verified_seller") or 0)

    # Validation
    if seller and not frappe.db.exists("AOS Seller", seller):
        return fail("Seller not found.", error="VALIDATION_ERROR")

    if price_type and price_type not in ALLOWED_PRICE_TYPES:
        return fail(
            "Invalid price_type.",
            error="VALIDATION_ERROR",
            data={"allowed": sorted(ALLOWED_PRICE_TYPES)},
        )

    if promotion_type and promotion_type not in ALLOWED_PROMOTIONS:
        return fail(
            "Invalid promotion_type.",
            error="VALIDATION_ERROR",
            data={"allowed": sorted(ALLOWED_PROMOTIONS)},
        )

    if price_min is not None and price_max is not None and price_min > price_max:
        return fail(
            "price_min cannot be greater than price_max.",
            error="VALIDATION_ERROR",
        )

    limit = max(1, min(_safe_int(kwargs.get("limit"), 20), 50))
    offset = max(0, _safe_int(kwargs.get("offset"), 0))

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
        "country": country,
        "today": today,
        "display_currency": display_currency,
        "base_currency": base_currency,
    }

    # Seller filter
    if seller:
        conditions.append("a.seller = %(seller)s")
        values["seller"] = seller

    # Category filter
    if category:
        category_ids = resolve_category_filter_values(category)

        if not category_ids:
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

        conditions.append("a.category in %(categories)s")
        values["categories"] = tuple(category_ids)

    # Search
    if q and len(q) >= 2:
        conditions.append("a.title LIKE %(q)s ESCAPE '\\'")
        values["q"] = safe_like_contains(q)

    # Price type
    if price_type:
        conditions.append("a.price_type = %(price_type)s")
        values["price_type"] = price_type

    # Rating
    if rating_min is not None:
        conditions.append("a.average_rating >= %(rating_min)s")
        values["rating_min"] = rating_min

    # Verified seller
    if verified_seller:
        conditions.append("COALESCE(p.is_verified, 0) = 1")

    # Promotions
    settings = get_aos_settings_snapshot()
    flash_window_days = settings.flash_sale_window_days

    offer_active_sql = """
        a.offer_price IS NOT NULL
        AND a.offer_price > 0
        AND (a.offer_start_date IS NULL OR a.offer_start_date <= %(today)s)
        AND (a.offer_end_date IS NULL OR a.offer_end_date >= %(today)s)
    """

    native_current_price_sql = f"""
        CASE
            WHEN {offer_active_sql}
            THEN a.offer_price
            ELSE a.price
        END
    """
    original_conversion = sql_conversion_expressions(amount_sql="a.price")
    current_conversion = sql_conversion_expressions(amount_sql=native_current_price_sql)
    original_price_sql = original_conversion["amount"]
    current_price_sql = current_conversion["amount"]

    if promotion_type in {"offer", "flash_sale", "deal"}:
        conditions.append(f"({offer_active_sql})")

    if promotion_type == "flash_sale":
        conditions.append(
            """
            a.offer_end_date IS NOT NULL
            AND a.offer_end_date BETWEEN %(today)s AND %(flash_end)s
            """
        )
        values["flash_end"] = add_days(today, flash_window_days)

    # Price range filters
    if price_min is not None:
        conditions.append(f"({original_conversion['available']}) = 1")
        conditions.append(f"{current_price_sql} >= %(price_min)s")
        values["price_min"] = price_min

    if price_max is not None:
        conditions.append(f"({original_conversion['available']}) = 1")
        conditions.append(f"{current_price_sql} <= %(price_max)s")
        values["price_max"] = price_max

    where_clause = " AND ".join(conditions)

    # Sorting
    verified_boost = "COALESCE(p.is_verified, 0) DESC"
    country_boost = "CASE WHEN a.country = %(country)s THEN 0 ELSE 1 END"
    geo_boost_parts = [country_boost]

    if location:
        values["location"] = location
        geo_boost_parts.append(
            "CASE WHEN a.location = %(location)s THEN 0 ELSE 1 END"
        )

    geo_boost = ", ".join(geo_boost_parts)

    if promotion_type == "deal":
        order_by = (
            f"{geo_boost}, "
            f"{verified_boost}, "
            "IFNULL(a.offer_percent, 0) DESC, "
            "w.creation DESC"
        )

    elif sort == "rating_high":
        order_by = (
            f"{geo_boost}, "
            f"{verified_boost}, "
            "a.average_rating DESC, "
            "a.total_reviews DESC, "
            "w.creation DESC"
        )

    elif sort == "recent":
        order_by = (
            f"{geo_boost}, "
            f"{verified_boost}, "
            "w.creation DESC"
        )

    elif sort == "price_low":
        order_by = (
            f"{geo_boost}, "
            f"{verified_boost}, "
            f"{original_conversion['available']} DESC, {current_price_sql} ASC, "
            "w.creation DESC"
        )

    elif sort == "price_high":
        order_by = (
            f"{geo_boost}, "
            f"{verified_boost}, "
            f"{original_conversion['available']} DESC, {current_price_sql} DESC, "
            "w.creation DESC"
        )

    # SQL Query
    sql = f"""
        SELECT
            a.name,
            a.title,
            a.status,
            a.country,
            a.location,
            a.category,
            a.seller,
            a.currency,
            {original_conversion["currency"]} as display_currency,
            %(display_currency)s as requested_display_currency,
            {original_conversion["available"]} as conversion_available,
            {original_conversion["rate"]} as conversion_rate,
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
            COALESCE(p.is_verified, 0) AS is_verified,
            {original_price_sql} as original_price_converted,
            {current_price_sql} as current_price
        FROM `tabAOS Wishlist` w
        INNER JOIN `tabAOS Ad` a ON a.name = w.ad
        INNER JOIN `tabAOS Seller` s ON s.name = a.seller
        INNER JOIN `tabAOS Profile` p ON p.user = s.user
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

    try:
        rows = frappe.db.sql(sql, values, as_dict=True)

        # Images
        ad_names = [row["name"] for row in rows]

        images_by_ad: Dict[str, List[Dict[str, Any]]] = {
            name: [] for name in ad_names
        }

        if ad_names:
            image_rows = frappe.get_all(
                "AOS Ad Image",
                filters={
                    "parenttype": "AOS Ad",
                    "parent": ["in", ad_names],
                },
                fields=["parent", "media", "image", "is_primary", "sort_order"],
                order_by="is_primary desc, sort_order asc",
            )

            for image in image_rows:
                images_by_ad.setdefault(image["parent"], []).append(image)

        items = []

        for row in rows:
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
            error="INTERNAL_ERROR",
        )
