"""
Search Ads by Image (implementation).

Guest accessible.
Rate limited by IP.
"""

from __future__ import annotations

from typing import List

import frappe

from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from aos.api.ads.serializers import serialize_ad_list_item
from aos.api.ads.constants import SEARCH_BY_IMAGE_LIMIT_PER_MINUTE_PER_IP

from aos.services.image_search_service import search_similar_ads


def search_ads_by_image_impl(**kwargs):
    """Search ads using an image."""
    rl = rate_limit(
        key=f"aos:ads:image_search:ip:{frappe.local.request_ip}",
        ttl_seconds=60,
        limit=SEARCH_BY_IMAGE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many search requests. Please try again shortly.",
    )

    if rl:
        return rl

    file = frappe.request.files.get("image")

    if not file:
        return fail("Image file is required.", code="VALIDATION_ERROR")

    try:
        # Get ranked ad_ids
        ad_ids: List[str] = search_similar_ads(file)

        if not ad_ids:
            return ok("No matching ads found.", data={"items": []})

        # Fetch minimal rows
        rows = frappe.get_all(
            "AOS Ad",
            filters={
                "name": ["in", ad_ids],
                "status": "Active",
            },
            fields=["name"],
        )

        if not rows:
            return ok("No matching ads found.", data={"items": []})

        # Load docs
        docs = [frappe.get_doc("AOS Ad", r.name) for r in rows]

        # Preserve ranking order
        doc_map = {d.name: d for d in docs}
        ordered_docs = [doc_map[aid] for aid in ad_ids if aid in doc_map]

        # Serialize
        results = [
            serialize_ad_list_item(doc)
            for doc in ordered_docs
        ]

        return ok("Search successful.", data={"items": results})

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Search Ads by Image Failed",
        )

        return fail(
            "Failed to search ads.",
            code="INTERNAL_ERROR",
        )
