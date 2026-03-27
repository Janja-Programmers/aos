"""
List Ads for buyers.

Rules:
 - Only Active ads are returned
 - Seller must be Active
 - Country isolation is strictly enforced
 - Expired ads are excluded in real-time
 - Optional filters supported
 - Sorting and filtering operate in display currency
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from frappe.utils import nowdate, add_days, getdate

from aos.api.shared.auth import current_user
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.utils import get_active_wishlist_ad_ids
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.utils.aos_settings import get_aos_settings_snapshot

from .constants import LIST_ADS_LIMIT_PER_MINUTE_PER_IP
from .category_filters import resolve_category_filter_values
from .serializers import serialize_ad_list_item


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


def list_ads_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:ads:list:ip:{request_ip()}",
        ttl_seconds=60,
        limit=LIST_ADS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )

    if rl:
        return rl

    # Market Context
    country, display_currency, error = resolve_market_context(
        country=kwargs.get("country"),
        currency=kwargs.get("currency"),
    )
    if error:
        return error

    user = current_user()
    today = getdate(nowdate())

    # Inputs
    location = str(kwargs.get("location") or "").strip()
    category = str(kwargs.get("category") or "").strip()
    seller = str(kwargs.get("seller") or "").strip()
    q = str(kwargs.get("q") or "").strip()

    sort = str(kwargs.get("sort") or "rating_high").strip() or "rating_high"

    price_type = str(kwargs.get("price_type") or "").strip()
    promotion_type = str(kwargs.get("promotion_type") or "").strip()

    price_min = _safe_float(kwargs.get("price_min"))
    price_max = _safe_float(kwargs.get("price_max"))
    rating_min = _safe_float(kwargs.get("rating_min"))
    verified_seller = int(kwargs.get("verified_seller") or 0)

    # Validation
    if seller and not frappe.db.exists("AOS Seller", seller):
        return fail("Seller not found.", code="VALIDATION_ERROR")

    if sort not in ALLOWED_SORTS:
        return fail(
            "Invalid sort.",
            code="VALIDATION_ERROR",
            data={"allowed": sorted(ALLOWED_SORTS)},
        )

    if price_type and price_type not in ALLOWED_PRICE_TYPES:
        return fail(
            "Invalid price_type.",
            code="VALIDATION_ERROR",
            data={"allowed": sorted(ALLOWED_PRICE_TYPES)},
        )

    if promotion_type and promotion_type not in ALLOWED_PROMOTIONS:
        return fail(
            "Invalid promotion_type.",
            code="VALIDATION_ERROR",
            data={"allowed": sorted(ALLOWED_PROMOTIONS)},
        )

    if price_min is not None and price_max is not None and price_min > price_max:
        return fail(
            "price_min cannot be greater than price_max.",
            code="VALIDATION_ERROR",
        )

    limit = max(1, min(_safe_int(kwargs.get("limit"), 20), 50))
    offset = max(0, _safe_int(kwargs.get("offset"), 0))

    # Base Conditions
    conditions = [
        "a.status = 'Active'",
        "a.country = %(country)s",
        "s.status = 'Active'",
        "(a.expires_on IS NULL OR a.expires_on >= %(today)s)",
    ]

    values: Dict[str, Any] = {
        "country": country,
        "today": today,
        "display_currency": display_currency,
    }

    # Seller filter
    if seller:
        conditions.append("a.seller = %(seller)s")
        values["seller"] = seller

    # Location filter
    if location:
        conditions.append("a.location = %(location)s")
        values["location"] = location

    # Category filter
    if category:
        category_ids = resolve_category_filter_values(category)

        if not category_ids:
            return ok(
                "Ads fetched.",
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
        conditions.append("a.title like %(q)s")
        values["q"] = f"%{q}%"

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
        conditions.append("s.is_verified = 1")

    # Promotions
    settings = get_aos_settings_snapshot()
    flash_window_days = settings.flash_sale_window_days

    offer_active_sql = """
        a.offer_price IS NOT NULL
        AND a.offer_price > 0
        AND (a.offer_start_date IS NULL OR a.offer_start_date <= %(today)s)
        AND (a.offer_end_date IS NULL OR a.offer_end_date >= %(today)s)
    """

    conversion_ratio = """
        (
            IFNULL(er_target.rate_vs_base,1)
            /
            IFNULL(er_source.rate_vs_base,1)
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
        conditions.append(f"{current_price_sql} >= %(price_min)s")
        values["price_min"] = price_min

    if price_max is not None:
        conditions.append(f"{current_price_sql} <= %(price_max)s")
        values["price_max"] = price_max

    where_clause = " AND ".join(conditions)

    # Sorting
    verified_boost = "(s.is_verified = 1) desc"

    if promotion_type == "deal":
        order_by = f"{verified_boost}, IFNULL(a.offer_percent,0) desc, a.creation desc"

    elif sort == "rating_high":
        order_by = f"{verified_boost}, a.average_rating desc, a.total_reviews desc, a.creation desc"

    elif sort == "recent":
        order_by = f"{verified_boost}, a.creation desc"

    elif sort == "price_low":
        order_by = f"{verified_boost}, {current_price_sql} asc, a.creation desc"

    elif sort == "price_high":
        order_by = f"{verified_boost}, {current_price_sql} desc, a.creation desc"

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
            %(display_currency)s as display_currency,
            a.price_type,
            a.price,
            a.offer_price,
            a.offer_start_date,
            a.offer_end_date,
            a.offer_percent,
            a.price_unit,
            a.average_rating,
            a.total_reviews,
            a.creation,
            s.is_verified,
            {original_price_sql} as original_price_converted,
            {current_price_sql} as current_price
        FROM `tabAOS Ad` a
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

    try:
        rows = frappe.db.sql(sql, values, as_dict=True)

        # Wishlist
        wishlisted_ids = set()

        if rows and user != "Guest":
            wishlisted_ids = get_active_wishlist_ad_ids(user)

        # Images
        ad_names = [row["name"] for row in rows]

        images_by_ad: Dict[str, List[Dict[str, Any]]] = {name: [] for name in ad_names}

        if ad_names:
            image_rows = frappe.get_all(
                "AOS Ad Image",
                filters={"parenttype": "AOS Ad", "parent": ["in", ad_names]},
                fields=["parent", "image", "is_primary", "sort_order"],
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
                    is_wishlisted=ad_doc.name in wishlisted_ids,
                )
            )

        return ok(
            "Ads fetched.",
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
            "AOS List Ads FX Failed",
        )

        return fail(
            "Failed to fetch ads.",
            code="INTERNAL_ERROR",
        )
