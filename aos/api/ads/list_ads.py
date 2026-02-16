"""
List Ads for buyers.

Rules:
 - Only Active ads are returned (cannot override)
 - country is required
 - Optional filters:
     location
     category
     promotion_type (offer | deal | flash_sale)
     price_type
     price_min / price_max (applied on current price)
     rating_min
     q (search in title)
 - Default sort: rating_high
 - Pagination: limit (1–50), offset
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from frappe.utils import nowdate, add_days, getdate

from aos.api.shared.auth import current_user
from aos.api.shared.utils import get_active_wishlist_ad_ids
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from .constants import LIST_ADS_LIMIT_PER_MINUTE_PER_IP
from .category_filters import resolve_category_filter_values
from .serializers import serialize_ad_list_item


ALLOWED_SORTS = {"rating_high", "price_low", "price_high", "recent"}
ALLOWED_PRICE_TYPES = {"Fixed", "Negotiable", "Contact for price", "Free"}
ALLOWED_PROMOTIONS = {"offer", "deal", "flash_sale"}


def _safe_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except Exception:
        return default


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        s = str(val).strip()
        if s == "":
            return None
        return float(s)
    except Exception:
        return None


def _get_country_from_prefs(user: str) -> str:
    if not user:
        return ""
    try:
        pref = frappe.db.get_value("AOS User Preference", {"user": user}, "country")
        return str(pref or "").strip()
    except Exception:
        return ""


def list_ads_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:ads:list:ip:{request_ip()}",
        ttl_seconds=60,
        limit=LIST_ADS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    # Inputs
    country = str(kwargs.get("country") or "").strip()
    location = str(kwargs.get("location") or "").strip()
    category = str(kwargs.get("category") or "").strip()

    q = str(kwargs.get("q") or "").strip()
    sort = str(kwargs.get("sort") or "rating_high").strip() or "rating_high"
    price_type = str(kwargs.get("price_type") or "").strip()
    promotion_type = str(kwargs.get("promotion_type") or "").strip()
    price_min = _safe_float(kwargs.get("price_min"))
    price_max = _safe_float(kwargs.get("price_max"))
    rating_min = _safe_float(kwargs.get("rating_min"))

    user = current_user()
    if not country and user != "Guest":
        country = _get_country_from_prefs(user)

    if not country:
        return fail("Country is required.", code="VALIDATION_ERROR")

    # Validation
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
        return fail("price_min cannot be greater than price_max.", code="VALIDATION_ERROR")

    limit = max(1, min(_safe_int(kwargs.get("limit"), 20), 50))
    offset = max(0, _safe_int(kwargs.get("offset"), 0))

    today = getdate(nowdate())
    conditions = ["status = 'Active'", "country = %(country)s"]
    values: Dict[str, Any] = {"country": country}

    if location:
        conditions.append("location = %(location)s")
        values["location"] = location

    if category:
        cats = resolve_category_filter_values(category)
        if not cats:
            return ok(
                "Ads fetched.",
                data={"items": [], "pagination": {"limit": limit, "offset": offset, "returned": 0}},
            )
        conditions.append("category in %(categories)s")
        values["categories"] = tuple(cats)

    if q and len(q) >= 2:
        conditions.append("title like %(q)s")
        values["q"] = f"%{q}%"

    if price_type:
        conditions.append("price_type = %(price_type)s")
        values["price_type"] = price_type

    if rating_min is not None:
        conditions.append("average_rating >= %(rating_min)s")
        values["rating_min"] = rating_min

    # Promotion conditions
    offer_active_sql = """
        offer_price IS NOT NULL
        AND offer_price > 0
        AND (offer_start_date IS NULL OR offer_start_date <= %(today)s)
        AND (offer_end_date IS NULL OR offer_end_date >= %(today)s)
    """

    if promotion_type in {"offer", "deal"}:
        conditions.append(f"({offer_active_sql})")

    if promotion_type == "flash_sale":
        conditions.append(f"""
            ({offer_active_sql})
            AND offer_end_date IS NOT NULL
            AND offer_end_date BETWEEN %(today)s AND %(flash_end)s
        """)
        values["flash_end"] = add_days(today, 7)

    values["today"] = today

    # Current Price
    current_price_sql = f"""
        CASE
            WHEN {offer_active_sql}
            THEN offer_price
            ELSE price
        END
    """

    # Price range applied on current_price
    if price_min is not None:
        conditions.append(f"{current_price_sql} >= %(price_min)s")
        values["price_min"] = price_min

    if price_max is not None:
        conditions.append(f"{current_price_sql} <= %(price_max)s")
        values["price_max"] = price_max

    where_clause = " AND ".join(conditions)

    # Sorting
    if sort == "rating_high":
        order_by = "average_rating desc, total_reviews desc, creation desc"

    elif sort == "recent":
        order_by = "creation desc"

    elif sort == "price_low":
        order_by = f"{current_price_sql} asc, creation desc"

    elif sort == "price_high":
        order_by = f"{current_price_sql} desc, creation desc"

    # Deal override (highest discount first)
    if promotion_type == "deal":
        order_by = "offer_percent desc, creation desc"

    # Final SQL
    sql = f"""
        SELECT
            name,
            title,
            status,
            country,
            location,
            category,
            currency,
            price_type,
            price,
            offer_price,
            offer_start_date,
            offer_end_date,
            offer_percent,
            price_unit,
            average_rating,
            total_reviews,
            creation,
            {current_price_sql} as current_price
        FROM `tabAOS Ad`
        WHERE {where_clause}
        ORDER BY {order_by}
        LIMIT %(limit)s OFFSET %(offset)s
    """

    values["limit"] = limit
    values["offset"] = offset

    try:
        rows = frappe.db.sql(sql, values, as_dict=True)
        wishlisted_ids = set()
        if rows:
            wishlisted_ids = get_active_wishlist_ad_ids(user)

        # Batch image fetch
        ad_names = [r["name"] for r in rows]
        images_by_ad: Dict[str, List[Dict[str, Any]]] = {n: [] for n in ad_names}
        if ad_names:
            img_rows = frappe.get_all(
                "AOS Ad Image",
                filters={"parenttype": "AOS Ad", "parent": ["in", ad_names]},
                fields=["parent", "image", "is_primary", "sort_order"],
            )
            for img in img_rows:
                images_by_ad.setdefault(img["parent"], []).append(img)

        items = []
        for r in rows:
            ad_doc = frappe._dict(r)
            ad_doc.images = images_by_ad.get(r["name"], [])
            ad_doc.is_offer_active = bool(
                r.get("offer_price")
                and (r.get("offer_start_date") is None or r.get("offer_start_date") <= today)
                and (r.get("offer_end_date") is None or r.get("offer_end_date") >= today)
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
        frappe.log_error(frappe.get_traceback(), "AOS List Ads Failed")
        return fail("Failed to fetch ads.", code="INTERNAL_ERROR")
