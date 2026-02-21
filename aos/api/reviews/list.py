"""List Approved Reviews for an Ad (Market-isolated)."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.market_context import resolve_market_country
from aos.api.shared.responses import fail, ok


def list_reviews_impl(**kwargs):
    """List approved reviews for a specific Ad."""

    ad = kwargs.get("ad")

    try:
        limit = int(kwargs.get("limit") or 20)
        offset = int(kwargs.get("offset") or 0)
    except Exception:
        return fail("Invalid pagination values.", code="VALIDATION_ERROR")

    if not ad:
        return fail("Ad is required.", code="VALIDATION_ERROR")

    # Market enforcement
    country, error = resolve_market_country(kwargs.get("country"))
    if error:
        return error

    ad_country = frappe.db.get_value("AOS Ad", ad, "country")

    if not ad_country:
        return fail("Ad not found.", code="NOT_FOUND")

    if ad_country != country:
        return fail("Ad not found.", code="NOT_FOUND")

    try:
        reviews = frappe.get_all(
            "AOS Review",
            filters={
                "ad": ad,
                "status": "Approved"
            },
            fields=[
                "name",
                "rating",
                "title",
                "comment",
                "reviewer",
                "creation",
                "like_count",
                "dislike_count"
            ],
            order_by="creation desc",
            limit=limit,
            start=offset
        )

        total = frappe.db.count(
            "AOS Review",
            {"ad": ad, "status": "Approved"}
        )

        return ok(
            "Reviews fetched.",
            data={
                "items": reviews,
                "total": total,
                "limit": limit,
                "offset": offset
            }
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS List Reviews Failed")
        return fail("Failed to fetch reviews.", code="INTERNAL_ERROR")
