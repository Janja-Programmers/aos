"""Create a Review for an Ad."""

from __future__ import annotations

from typing import List

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import CREATE_REVIEW_LIMIT_PER_MINUTE_PER_USER
from .eligibility import (
    get_review_eligibility_for_ad,
    review_eligibility_error_response,
)
from .media import (
    attach_review_image_media,
    normalize_review_image_inputs,
    validate_review_images_for_create,
)


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
    images: List[str] = (
        kwargs.get("images")
        if kwargs.get("images") is not None
        else kwargs.get("review_images")
    ) or []

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

    image_media_ids, err = normalize_review_image_inputs(images)
    if err:
        return err

    validated_images, err = validate_review_images_for_create(
        media_ids=image_media_ids,
        user=current_user,
    )
    if err:
        return err

    ad_doc = frappe.db.get_value(
        "AOS Ad",
        ad,
        ["name", "seller", "status"],
        as_dict=True,
    )

    if not ad_doc or ad_doc.status != "Active":
        return fail("Ad not found.", code="NOT_FOUND")

    eligibility = get_review_eligibility_for_ad(
        ad_doc=ad_doc,
        current_user=current_user,
    )

    if not eligibility.get("can_review"):
        message, code = review_eligibility_error_response(eligibility)

        return fail(
            message,
            code=code,
            data={
                "review_viewer_state": eligibility,
            },
        )

    try:
        review = frappe.new_doc("AOS Review")
        review.ad = ad
        review.reviewer = current_user
        review.rating = rating
        review.comment = comment
        review.title = title

        for item in validated_images:
            child = review.append("review_images", {})
            child.media = item["media"]
            child.image = item["url"]

        review.insert(ignore_permissions=True)

        for item in validated_images:
            _media_doc, err = attach_review_image_media(
                media_id=item["media"],
                user=current_user,
                review_id=review.name,
            )
            if err:
                frappe.db.rollback()
                return err

        return ok(
            "Review submitted and pending approval.",
            data={
                "id": review.name,
                "images": [
                    {
                        "media": item["media"],
                        "media_id": item["media"],
                        "image": item["url"],
                        "url": item["url"],
                    }
                    for item in validated_images
                ],
                "review_viewer_state": {
                    "can_review": False,
                    "reason": "ALREADY_REVIEWED",
                    "has_reviewed": True,
                    "has_communicated": True,
                },
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "AOS Create Review Failed")
        return fail("Failed to create review.", code="INTERNAL_ERROR")
