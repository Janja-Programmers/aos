"""Toggle Reaction on a Review.

Supports:
  - Like
  - Unlike
  - Dislike
  - Undislike
  - Switching between Like/Dislike
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import TOGGLE_REACTION_LIMIT_PER_MINUTE_PER_USER
from aos.aos.doctype.aos_review_reaction.aos_review_reaction import update_review_reaction_counts


def toggle_reaction_impl(**kwargs):
    """Toggle Like / Dislike on a Review."""

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

    review = kwargs.get("review")
    reaction = kwargs.get("reaction")

    if not review:
        return fail("Review is required.", code="VALIDATION_ERROR")

    if reaction not in ["Like", "Dislike"]:
        return fail("Invalid reaction.", code="VALIDATION_ERROR")

    try:
        review_doc = frappe.get_doc("AOS Review", review)

        if review_doc.status != "Approved":
            return fail("You can only react to approved reviews.", code="VALIDATION_ERROR")

        if review_doc.reviewer == current_user:
            return fail("You cannot react to your own review.", code="VALIDATION_ERROR")

        existing = frappe.get_all(
            "AOS Review Reaction",
            filters={
                "review": review,
                "user": current_user
            },
            fields=["name", "reaction"],
            limit=1
        )

        # No reaction yet → Create
        if not existing:
            doc = frappe.new_doc("AOS Review Reaction")
            doc.review = review
            doc.reaction = reaction
            doc.insert(ignore_permissions=True)
            frappe.db.commit()

            return ok("Reaction added.", data={"status": "added", "reaction": reaction})

        doc = frappe.get_doc("AOS Review Reaction", existing[0].name)

        # Same reaction → Remove (Unlike / Undislike)
        if doc.reaction == reaction:
            review_id = doc.review
            doc.delete(ignore_permissions=True)
            frappe.db.commit()
            update_review_reaction_counts(review_id)

            return ok("Reaction removed.", data={"status": "removed"})

        # Switch reaction
        doc.reaction = reaction
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        return ok("Reaction updated.", data={"status": "switched", "reaction": reaction})

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Toggle Reaction Failed")
        return fail("Failed to toggle reaction.", code="INTERNAL_ERROR")
