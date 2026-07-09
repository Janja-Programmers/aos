"""Search/ranking recommendation endpoint implementations."""

from __future__ import annotations

import frappe

from aos.api.ads.serializers import serialize_ad_list_item
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import require_id
from aos.services.search_ranking_service import related_ad_candidates


def related_ads_impl(**kwargs):
    """Return related active ads using search-ranking candidates and Frappe hydration."""
    ad_id, err = require_id(kwargs.get("ad_id") or kwargs.get("id"), "ad_id")
    if err:
        return err

    try:
        limit = max(1, min(int(kwargs.get("limit") or 20), 50))
    except Exception:
        limit = 20

    try:
        if not frappe.db.exists("AOS Ad", ad_id):
            return fail("Ad not found.", error="NOT_FOUND")

        source = frappe.get_doc("AOS Ad", ad_id)
        if source.status != "Active":
            return ok(
                "Related ads fetched.",
                data={
                    "items": [],
                    "reason": "SOURCE_AD_NOT_ACTIVE",
                },
            )

        candidate_ids = related_ad_candidates(ad_id=ad_id, limit=limit)
        if not candidate_ids:
            return ok("Related ads fetched.", data={"items": []})

        docs = []
        for candidate_id in candidate_ids:
            if not frappe.db.exists("AOS Ad", candidate_id):
                continue
            doc = frappe.get_doc("AOS Ad", candidate_id)
            if doc.status != "Active":
                continue
            docs.append(doc)

        return ok(
            "Related ads fetched.",
            data={"items": [serialize_ad_list_item(doc) for doc in docs]},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Related ads search/ranking failed")
        return fail("Failed to fetch related ads.", error="INTERNAL_ERROR")
