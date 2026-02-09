"""List Ads for buyers.

Only Active ads should be visible by default.
Filters:
 - country (required for meaningful browsing; if omitted and user is logged in,
   we try to infer from user preferences)
 - location (optional)
 - category (optional)

Pagination:
 - limit (default 20, max 50)
 - offset (default 0)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from .constants import LIST_ADS_LIMIT_PER_MINUTE_PER_IP
from .category_filters import resolve_category_filter_values
from .serializers import serialize_ad_list_item


ALLOWED_SORTS = {"recent", "price_low", "price_high"}
ALLOWED_PRICE_TYPES = {"Fixed", "Negotiable", "Contact for price", "Free"}
PRICED_TYPES = {"Fixed", "Negotiable"}


def _safe_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except Exception:
        return default


def _safe_float(val: Any) -> Optional[float]:
    """Parse float safely.

    Returns:
        float if value looks numeric, otherwise None.
    """

    if val is None:
        return None
    try:
        s = str(val).strip()
        if s == "":
            return None
        return float(s)
    except Exception:
        return None


def _order_by_for_sort(sort: str) -> str:
    """Return a safe SQL order_by snippet for the supported sorts."""

    sort = (sort or "").strip() or "recent"
    if sort not in ALLOWED_SORTS:
        sort = "recent"

    if sort == "recent":
        return "creation desc"

    # Price sorts:
    #  1) Put non-priced ads last (Free / Contact for price / null / <=0)
    #  2) Then sort by numeric price
    #  3) Tiebreaker: newest first
    non_priced_case = (
        "CASE WHEN price_type IN ('Free','Contact for price') OR price IS NULL OR price <= 0 "
        "THEN 1 ELSE 0 END"
    )
    if sort == "price_low":
        return f"{non_priced_case} asc, price asc, creation desc"
    return f"{non_priced_case} asc, price desc, creation desc"


def _get_country_from_prefs(user: str) -> str:
    """Return country from preferences, or empty string."""

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

    # filters
    status = str(kwargs.get("status") or "Active").strip() or "Active"
    country = str(kwargs.get("country") or "").strip()
    location = str(kwargs.get("location") or "").strip()
    category = str(kwargs.get("category") or "").strip()

    q = str(kwargs.get("q") or "").strip()
    sort = str(kwargs.get("sort") or "recent").strip() or "recent"
    price_type = str(kwargs.get("price_type") or "").strip()
    price_min_raw = kwargs.get("price_min")
    price_max_raw = kwargs.get("price_max")

    # If country omitted, try infer from logged-in user's prefs
    user = current_user()
    if not country and user != "Guest":
        country = _get_country_from_prefs(user)

    if not country:
        return fail("Country is required.", code="VALIDATION_ERROR")

    # Validate sort / price filters early
    if sort and sort not in ALLOWED_SORTS:
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

    price_min = _safe_float(price_min_raw)
    price_max = _safe_float(price_max_raw)
    if price_min_raw is not None and str(price_min_raw).strip() != "" and price_min is None:
        return fail("price_min must be a number.", code="VALIDATION_ERROR")
    if price_max_raw is not None and str(price_max_raw).strip() != "" and price_max is None:
        return fail("price_max must be a number.", code="VALIDATION_ERROR")
    if price_min is not None and price_max is not None and price_min > price_max:
        return fail("price_min cannot be greater than price_max.", code="VALIDATION_ERROR")

    limit = _safe_int(kwargs.get("limit"), 20)
    offset = _safe_int(kwargs.get("offset"), 0)
    limit = max(1, min(limit, 50))
    offset = max(0, offset)

    filters: Dict[str, Any] = {
        "status": status,
        "country": country,
    }
    if location:
        filters["location"] = location

    if category:
        cats = resolve_category_filter_values(category)
        if not cats:
            # Unknown/inactive category or a group with no children
            return ok(
                "Ads fetched.",
                data={
                    "items": [],
                    "pagination": {"limit": limit, "offset": offset, "returned": 0},
                },
            )
        filters["category"] = ["in", cats]

    # Search (title) - keep it simple and fast for v1
    if q and len(q) >= 2:
        filters["title"] = ["like", f"%{q}%"]

    # Price filters
    if price_type:
        filters["price_type"] = price_type

    # Apply price range only when it makes sense.
    # If price_type is Fixed/Negotiable (or unspecified), range matches numeric priced ads.
    range_requested = price_min is not None or price_max is not None
    if range_requested:
        if price_type and price_type not in PRICED_TYPES:
            # Buyer asked for a range but also pinned a non-numeric price_type
            return ok(
                "Ads fetched.",
                data={
                    "items": [],
                    "pagination": {"limit": limit, "offset": offset, "returned": 0},
                },
            )

        if not price_type:
            filters["price_type"] = ["in", list(PRICED_TYPES)]

        if price_min is not None and price_max is not None:
            filters["price"] = ["between", [price_min, price_max]]
        elif price_min is not None:
            filters["price"] = [">=", price_min]
        elif price_max is not None:
            filters["price"] = ["<=", price_max]

    try:
        rows = frappe.get_all(
            "AOS Ad",
            filters=filters,
            fields=[
                "name",
                "title",
                "status",
                "country",
                "location",
                "category",
                "currency",
                "price_type",
                "price",
                "price_unit",
                "creation",
            ],
            order_by=_order_by_for_sort(sort),
            start=offset,
            page_length=limit,
        )

        # fetch images for those ads (one query)
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

        items: List[Dict[str, Any]] = []
        for r in rows:
            # create a lightweight doc-like object
            ad_doc = frappe._dict(r)
            ad_doc.name = r["name"]
            ad_doc.images = images_by_ad.get(r["name"], [])
            items.append(serialize_ad_list_item(ad_doc))

        return ok(
            "Ads fetched.",
            data={
                "items": items,
                "pagination": {"limit": limit, "offset": offset, "returned": len(items)},
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS List Ads Failed")
        return fail("Failed to fetch ads.", code="INTERNAL_ERROR")
