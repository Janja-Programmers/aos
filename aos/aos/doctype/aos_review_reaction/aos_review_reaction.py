# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.reviews.aggregates import recompute_review_reaction_counts
from aos.services.reviews.constants import STATUS_APPROVED
from aos.services.reviews.validation import normalize_reaction
from aos.utils.doctype_permissions import has_doctype_permission


class AOSReviewReaction(Document):
    def before_insert(self):
        user = getattr(frappe.session, "user", None) or "Guest"
        if user == "Guest":
            frappe.throw("Login required")
        if not has_doctype_permission(user=user, doctype=self.doctype, ptype="create"):
            self.user = user

    def validate(self):
        self.reaction = normalize_reaction(self.reaction)
        review = frappe.db.get_value(
            "AOS Review", self.review, ["reviewer", "status"], as_dict=True
        )
        if not review or review.status != STATUS_APPROVED:
            frappe.throw("You can only react to approved reviews.")
        if review.reviewer == self.user:
            frappe.throw("You cannot react to your own review.")

    def after_insert(self):
        update_review_reaction_counts(self.review)

    def on_update(self):
        update_review_reaction_counts(self.review)

    def after_delete(self):
        update_review_reaction_counts(self.review)



def update_review_reaction_counts(review_name):
    """Backward-compatible DocType hook delegate."""
    return recompute_review_reaction_counts(review_id=review_name)
