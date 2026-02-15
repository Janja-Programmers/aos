from __future__ import annotations

from typing import Any, Dict, List

import frappe

from aos.api.shared.auth import current_user
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

    user = current_user()
    if user == "Guest":
        return fail("Authentication required.", code="AUTH_REQUIRED")

    try:
        limit = int(kwargs.get("limit", 20))
        offset = int(kwargs.get("offset", 0))
    except Exception:
        return fail("Invalid pagination values.", code="VALIDATION_ERROR")

    limit = max(1, min(limit, 50))
    offset = max(0, offset)

    rows = frappe.get_all(
        "AOS Wishlist",
        filters={"user": user, "status": "Active"},
        fields=["ad"],
        order_by="creation desc",
        start=offset,
        page_length=limit,
    )

    ad_ids = [r["ad"] for r in rows]
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

    ads = frappe.get_all(
        "AOS Ad",
        filters={"name": ["in", ad_ids], "status": "Active"},
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
    )

    ad_names = [r["name"] for r in ads]
    images_by_ad = {n: [] for n in ad_names}

    if ad_names:
        img_rows = frappe.get_all(
            "AOS Ad Image",
            filters={"parenttype": "AOS Ad", "parent": ["in", ad_names]},
            fields=["parent", "image", "is_primary", "sort_order"],
        )

        for img in img_rows:
            images_by_ad.setdefault(img["parent"], []).append(img)

    items = []

    for r in ads:
        ad_doc = frappe._dict(r)
        ad_doc.name = r["name"]
        ad_doc.images = images_by_ad.get(r["name"], [])

        item = serialize_ad_list_item(ad_doc, is_wishlisted=True)
        items.append(item)

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
