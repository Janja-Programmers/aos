"""List current user's Ads."""

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import nowdate, getdate

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import MY_ADS_LIMIT_PER_MINUTE_PER_USER
from .serializers import serialize_my_ad_list_item


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
    status = str(kwargs.get("status") or "").strip()

    seller = frappe.db.get_value(
        "AOS Seller",
        {"user": user},
        "name",
    )

    if not seller:
        return ok(
            "My ads fetched.",
            data={
                "items": [],
                "pagination": {"limit": limit, "offset": offset, "total": 0},
            },
        )

    today = getdate(nowdate())

    conditions = ["seller = %(seller)s"]
    values: Dict[str, Any] = {"seller": seller}

    if status:
        conditions.append("status = %(status)s")
        values["status"] = status

    where_clause = " AND ".join(conditions)

    # Offer logic
    offer_active_sql = """
        offer_price IS NOT NULL
        AND offer_price > 0
        AND (offer_start_date IS NULL OR offer_start_date <= %(today)s)
        AND (offer_end_date IS NULL OR offer_end_date >= %(today)s)
    """

    values["today"] = today

    # SQL
    sql = f"""
        SELECT
            name,
            title,
            country,
            location,
            status,
            currency,
            price,
            offer_price,
            offer_start_date,
            offer_end_date,
            creation,
            CASE
                WHEN {offer_active_sql}
                THEN offer_price
                ELSE price
            END as current_price
        FROM `tabAOS Ad`
        WHERE {where_clause}
        ORDER BY modified desc
        LIMIT %(limit)s OFFSET %(offset)s
    """

    values["limit"] = limit
    values["offset"] = offset

    try:
        rows = frappe.db.sql(sql, values, as_dict=True)

        ad_names: List[str] = [r["name"] for r in rows]

        images_by_ad: Dict[str, List[Dict[str, Any]]] = {name: [] for name in ad_names}

        if ad_names:
            image_rows = frappe.get_all(
                "AOS Ad Image",
                filters={
                    "parenttype": "AOS Ad",
                    "parent": ["in", ad_names],
                },
                fields=["parent", "image", "is_primary", "sort_order"],
                order_by="is_primary desc, sort_order asc",
            )

            for img in image_rows:
                images_by_ad.setdefault(img["parent"], []).append(img)

        items = []

        for row in rows:
            ad_doc = frappe._dict(row)

            ad_doc.images = images_by_ad.get(row["name"], [])

            items.append(
                serialize_my_ad_list_item(ad_doc)
            )

        total = frappe.db.count(
            "AOS Ad",
            filters={
                "seller": seller,
                **({"status": status} if status else {}),
            },
        )

        return ok(
            "My ads fetched.",
            data={
                "items": items,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": total,
                },
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS My Ads Failed")
        return fail("Failed to fetch ads.", code="INTERNAL_ERROR")
