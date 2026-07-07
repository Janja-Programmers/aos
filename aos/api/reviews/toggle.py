"""Toggle Reaction on a Review."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.db import is_duplicate_entry_error

from .constants import TOGGLE_REACTION_LIMIT_PER_MINUTE_PER_USER
from aos.aos.doctype.aos_review_reaction.aos_review_reaction import (
    update_review_reaction_counts,
)


def toggle_reaction_impl(**kwargs):
    """Toggle Like / Dislike on an approved Review."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:reviews:toggle:user:{current_user}",
        ttl_seconds=60,
        limit=TOGGLE_REACTION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    review = str(kwargs.get("review") or "").strip()
    reaction = str(kwargs.get("reaction") or "").strip()

    if not review:
        return fail("Review is required.", code="VALIDATION_ERROR")

    if reaction not in {"Like", "Dislike"}:
        return fail("Invalid reaction.", code="VALIDATION_ERROR")

    review_doc = frappe.db.get_value(
        "AOS Review",
        review,
        ["name", "status", "reviewer", "ad"],
        as_dict=True,
    )

    if not review_doc or review_doc.status != "Approved":
        return fail("Review not found.", code="NOT_FOUND")

    # Make sure the reviewed ad still exists and is visible.
    ad_status = frappe.db.get_value("AOS Ad", review_doc.ad, "status")

    if ad_status != "Active":
        return fail("Review not found.", code="NOT_FOUND")

    if review_doc.reviewer == current_user:
        return fail("You cannot react to your own review.", code="VALIDATION_ERROR")

    try:
        existing = frappe.get_all(
            "AOS Review Reaction",
            filters={
                "review": review,
                "user": current_user,
            },
            fields=["name", "reaction"],
            limit=1,
        )

        # No reaction yet: create reaction.
        if not existing:
            doc = frappe.new_doc("AOS Review Reaction")
            doc.review = review
            doc.reaction = reaction
            doc.insert(ignore_permissions=True)
            return ok(
                "Reaction added.",
                data={
                    "status": "added",
                    "reaction": reaction,
                },
            )

        doc = frappe.get_doc("AOS Review Reaction", existing[0].name)

        # Same reaction: remove reaction.
        if doc.reaction == reaction:
            review_id = doc.review
            doc.delete(ignore_permissions=True)
            update_review_reaction_counts(review_id)
            return ok(
                "Reaction removed.",
                data={
                    "status": "removed",
                },
            )

        # Different reaction: switch reaction.
        doc.reaction = reaction
        doc.save(ignore_permissions=True)

        return ok(
            "Reaction updated.",
            data={
                "status": "switched",
                "reaction": reaction,
            },
        )

    except Exception as ex:
        if is_duplicate_entry_error(ex):
            frappe.db.rollback()

            existing_reaction = frappe.db.get_value(
                "AOS Review Reaction",
                {
                    "review": review,
                    "user": current_user,
                },
                "reaction",
            )

            update_review_reaction_counts(review)

            return ok(
                "Reaction already exists.",
                data={
                    "status": "added",
                    "reaction": existing_reaction or reaction,
                },
            )

        if isinstance(ex, frappe.ValidationError):
            return fail(str(ex), code="VALIDATION_ERROR")

        frappe.log_error(frappe.get_traceback(), "AOS Toggle Reaction Failed")
        return fail("Failed to toggle reaction.", code="INTERNAL_ERROR")
