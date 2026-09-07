"""Search/ranking recommendation endpoint implementations."""

from __future__ import annotations

import frappe

from aos.api.ads.serializers import serialize_ad_list_item
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import require_id
from aos.services.search_ranking_service import related_ad_candidates


def _eligible_public_ad_ids(ad_ids: list[str]) -> set[str]:
    safe = tuple(dict.fromkeys(str(ad_id or "").strip() for ad_id in ad_ids if str(ad_id or "").strip()))
    if not safe:
        return set()
    rows = frappe.db.sql(
        """
        SELECT a.name
        FROM `tabAOS Ad` a
        INNER JOIN `tabAOS Seller` s ON s.name = a.seller
        INNER JOIN `tabAOS Profile` p ON p.user = s.user
        INNER JOIN `tabUser` u ON u.name = s.user
        WHERE a.name IN %(ad_ids)s
          AND a.status = 'Active'
          AND s.status = 'Active'
          AND u.enabled = 1
          AND COALESCE(p.is_deleted, 0) = 0
          AND COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'
        """,
        {"ad_ids": safe},
        pluck=True,
    )
    return {str(name) for name in rows if name}


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

        if ad_id not in _eligible_public_ad_ids([ad_id]):
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

        eligible = _eligible_public_ad_ids(list(candidate_ids))
        docs = [
            frappe.get_doc("AOS Ad", candidate_id)
            for candidate_id in candidate_ids
            if candidate_id in eligible
        ]

        return ok(
            "Related ads fetched.",
            data={"items": [serialize_ad_list_item(doc) for doc in docs]},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Related ads search/ranking failed")
        return fail("Failed to fetch related ads.", error="INTERNAL_ERROR")
