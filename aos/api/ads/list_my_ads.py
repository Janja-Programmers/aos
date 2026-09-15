"""Bounded seller-owned Ads listing."""

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import getdate, nowdate

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.ads.api import run_ads_api
from aos.services.ads.authorization import get_seller_for_user
from aos.services.ads.constants import AD_STATUSES, LIST_MY_AD_FIELDS, MAX_IMAGES
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import ensure_known_fields, normalize_pagination, normalize_text

from .constants import MY_ADS_LIMIT_PER_MINUTE_PER_USER
from .media import project_ad_image_urls
from .serializers import serialize_my_ad_list_item


def list_my_ads_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(
        key=f"aos:ads:my:user:{user}",
        ttl_seconds=60,
        limit=MY_ADS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    def _list():
        ensure_known_fields(kwargs, LIST_MY_AD_FIELDS)
        limit, offset = normalize_pagination(kwargs)
        status = normalize_text(kwargs.get("status"), field="status", max_length=40)
        if status and status not in AD_STATUSES:
            raise AdsValidationError("Invalid ad status.", code="INVALID_AD_INPUT")
        seller = get_seller_for_user(user)
        if not seller:
            return ok(
                "My ads fetched.",
                data={"items": [], "pagination": {"limit": limit, "offset": offset, "total": 0}},
            )

        conditions = ["seller = %(seller)s"]
        values: Dict[str, Any] = {
            "seller": seller.name,
            "today": getdate(nowdate()),
            "limit": limit,
            "offset": offset,
        }
        if status:
            conditions.append("status = %(status)s")
            values["status"] = status
        rows = frappe.db.sql(
            f"""
            SELECT name, public_id, title, country, location, status, currency, price,
                   offer_price, offer_start_date, offer_end_date, creation, modified,
                   CASE
                     WHEN offer_price IS NOT NULL AND offer_price > 0
                      AND (offer_start_date IS NULL OR offer_start_date <= %(today)s)
                      AND (offer_end_date IS NULL OR offer_end_date >= %(today)s)
                     THEN offer_price ELSE price
                   END AS current_price
            FROM `tabAOS Ad`
            WHERE {' AND '.join(conditions)}
            ORDER BY modified DESC, name DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            values,
            as_dict=True,
        )
        names = [row.name for row in rows]
        images_by_ad: Dict[str, List[Dict[str, Any]]] = {name: [] for name in names}
        if names:
            for image in frappe.get_all(
                "AOS Ad Image",
                filters={"parenttype": "AOS Ad", "parent": ["in", names]},
                fields=["parent", "media", "is_primary", "sort_order"],
                order_by="parent asc, is_primary desc, sort_order asc, name asc",
                limit=max(1, len(names) * MAX_IMAGES),
            ):
                images_by_ad.setdefault(image.parent, []).append(image)
        project_ad_image_urls([image for images in images_by_ad.values() for image in images])
        items = []
        for row in rows:
            row.images = images_by_ad.get(row.name, [])
            items.append(serialize_my_ad_list_item(row))
        total = frappe.db.count(
            "AOS Ad",
            {"seller": seller.name, **({"status": status} if status else {})},
        )
        return ok(
            "My ads fetched.",
            data={"items": items, "pagination": {"limit": limit, "offset": offset, "total": total}},
        )

    return run_ads_api(_list, fallback="Failed to fetch ads.", log_title="AOS My Ads Failed")
