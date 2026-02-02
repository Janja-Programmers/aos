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
from .serializers import serialize_ad_list_item


def _safe_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except Exception:
        return default


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

    # If country omitted, try infer from logged-in user's prefs
    user = current_user()
    if not country and user != "Guest":
        country = _get_country_from_prefs(user)

    if not country:
        return fail("Country is required.", code="VALIDATION_ERROR")

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
        filters["category"] = category

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
            order_by="creation desc",
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
