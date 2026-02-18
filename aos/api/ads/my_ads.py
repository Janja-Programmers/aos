"""List current user's Ads."""

from __future__ import annotations

from typing import Any, Dict

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import MY_ADS_LIMIT_PER_MINUTE_PER_USER
from .serializers import serialize_ad_list_item


def _safe_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except Exception:
        return default


def my_ads_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:ads:my:user:{user}",
        ttl_seconds=60,
        limit=MY_ADS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    limit = min(max(_safe_int(kwargs.get("limit"), 20), 1), 50)
    offset = max(_safe_int(kwargs.get("offset"), 0), 0)
    status = str(kwargs.get("status" or "") or "").strip()

    filters: Dict[str, Any] = {"user": user}
    if status:
        filters["status"] = status

    try:
        rows = frappe.get_all(
            "AOS Ad",
            filters=filters,
            fields=[
                "name",
                "title",
                "country",
                "location",
                "category",
                "status",
                "price_type",
                "price",
                "currency",
                "price_unit",
                "creation",
            ],
            order_by="modified desc",
            limit_start=offset,
            limit_page_length=limit,
        )

        items = []
        # Load docs only when needed to get primary image, but keep it light.
        for r in rows:
            try:
                doc = frappe.get_doc("AOS Ad", r.name)
                items.append(serialize_ad_list_item(doc))
            except Exception:
                # Fallback to minimal row if doc fetch fails
                items.append({"id": r.name, "title": r.title})

        total = frappe.db.count("AOS Ad", filters=filters)

        return ok(
            "My ads fetched.",
            data={"items": items, "pagination": {"limit": limit, "offset": offset, "total": total}},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS My Ads Failed")
        return fail("Failed to fetch ads.", code="INTERNAL_ERROR")
