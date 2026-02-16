"""List Reviews for an Ad (Approved only)."""

from __future__ import annotations

from typing import Any, Dict

import frappe

from aos.api.shared.responses import fail, ok


def list_reviews_impl(**kwargs):
    """List approved reviews for a specific Ad."""

    ad = kwargs.get("ad")
    limit = int(kwargs.get("limit") or 20)
    offset = int(kwargs.get("offset") or 0)

    if not ad:
        return fail("Ad is required.", code="VALIDATION_ERROR")

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
