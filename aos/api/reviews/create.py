"""Create a Review for an Ad.

Client should already:
  1) Selected an Ad
  2) Provided rating (1–5)
  3) Provided comment
  4) Uploaded images (optional) via /api/method/upload_file

Server-side validations also happen inside AOS Review DocType.
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import CREATE_REVIEW_LIMIT_PER_MINUTE_PER_USER


def create_review_impl(**kwargs):
    """Create an AOS Review (Pending approval)."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:reviews:create:user:{current_user}",
        ttl_seconds=60,
        limit=CREATE_REVIEW_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    ad = kwargs.get("ad")
    rating = kwargs.get("rating")
    comment = kwargs.get("comment")
    title = kwargs.get("title")
    images = kwargs.get("images") or []

    if not ad:
        return fail("Ad is required.", code="VALIDATION_ERROR")

    if not rating:
        return fail("Rating is required.", code="VALIDATION_ERROR")

    if not comment:
        return fail("Comment is required.", code="VALIDATION_ERROR")

    try:
        ad_doc = frappe.get_doc("AOS Ad", ad)

        # Prevent reviewing own ad
        if ad_doc.user == current_user:
            return fail("You cannot review your own ad.", code="VALIDATION_ERROR")

        # Prevent duplicate review
        existing = frappe.get_all(
            "AOS Review",
            filters={
                "ad": ad,
                "reviewer": current_user
            },
            limit=1
        )

        if existing:
            return fail("You have already reviewed this ad.", code="VALIDATION_ERROR")

        review = frappe.new_doc("AOS Review")
        review.ad = ad
        review.rating = rating
        review.comment = comment
        review.title = title

        # Append images (if any)
        for img in images:
            child = review.append("review_images", {})
            child.image = img

        review.insert(ignore_permissions=True)

        frappe.db.commit()

        return ok(
            "Review submitted and pending approval.",
            data={"id": review.name}
        )

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Create Review Failed")
        return fail("Failed to create review.", code="INTERNAL_ERROR")
