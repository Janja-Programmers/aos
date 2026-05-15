"""Create a Review for an Ad."""

from __future__ import annotations

from typing import List

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

    ad = str(kwargs.get("ad") or "").strip()
    rating = kwargs.get("rating")
    comment = str(kwargs.get("comment") or "").strip()
    title = str(kwargs.get("title") or "").strip()
    images: List[str] = kwargs.get("images") or []

    if not ad:
        return fail("Ad is required.", code="VALIDATION_ERROR")

    if rating is None:
        return fail("Rating is required.", code="VALIDATION_ERROR")

    if not comment:
        return fail("Comment is required.", code="VALIDATION_ERROR")

    # Validate rating range
    try:
        rating = float(rating)
    except Exception:
        return fail("Invalid rating.", code="VALIDATION_ERROR")

    if rating < 1 or rating > 5:
        return fail("Rating must be between 1 and 5.", code="VALIDATION_ERROR")

    # Optional image limit
    if len(images) > 5:
        return fail("Maximum 5 images allowed.", code="VALIDATION_ERROR")

    ad_doc = frappe.db.get_value(
        "AOS Ad",
        ad,
        ["name", "seller", "status"],
        as_dict=True,
    )

    if not ad_doc or ad_doc.status != "Active":
        return fail("Ad not found.", code="NOT_FOUND")

    # Prevent reviewing own ad
    seller_user = frappe.db.get_value(
        "AOS Seller",
        ad_doc.seller,
        "user",
    )

    if seller_user == current_user:
        return fail("You cannot review your own ad.", code="VALIDATION_ERROR")

    # Prevent duplicate review
    if frappe.db.exists(
        "AOS Review",
        {
            "ad": ad,
            "reviewer": current_user,
        },
    ):
        return fail("You have already reviewed this ad.", code="VALIDATION_ERROR")

    # Create review
    try:
        review = frappe.new_doc("AOS Review")
        review.ad = ad
        review.reviewer = current_user
        review.rating = rating
        review.comment = comment
        review.title = title

        for img in images:
            child = review.append("review_images", {})
            child.image = img

        review.insert(ignore_permissions=True)

        return ok(
            "Review submitted and pending approval.",
            data={"id": review.name},
        )

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Create Review Failed")
        return fail("Failed to create review.", code="INTERNAL_ERROR")
