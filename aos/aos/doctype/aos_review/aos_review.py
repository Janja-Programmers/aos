# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.reviews.aggregates import recompute_review_aggregates
from aos.services.reviews.constants import (
    ALL_STATUSES,
    STATUS_APPROVED,
    STATUS_HIDDEN,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_WITHDRAWN,
)
from aos.services.reviews.eligibility import review_key
from aos.services.reviews.validation import normalize_comment, normalize_rating, normalize_title

_ALLOWED_TRANSITIONS = {
    STATUS_PENDING: {STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_HIDDEN, STATUS_WITHDRAWN},
    STATUS_APPROVED: {STATUS_APPROVED, STATUS_PENDING, STATUS_HIDDEN, STATUS_WITHDRAWN},
    STATUS_REJECTED: {STATUS_REJECTED, STATUS_PENDING, STATUS_WITHDRAWN},
    STATUS_HIDDEN: {STATUS_HIDDEN, STATUS_APPROVED, STATUS_REJECTED, STATUS_WITHDRAWN},
    STATUS_WITHDRAWN: {STATUS_WITHDRAWN},
}


class AOSReview(Document):
    def before_insert(self):
        user = getattr(frappe.session, "user", None) or "Guest"
        if user == "Guest":
            frappe.throw("Login required")
        if "System Manager" not in set(frappe.get_roles(user) or []):
            self.reviewer = user
            self.status = STATUS_PENDING
        self.review_key = review_key(reviewer=self.reviewer, ad_id=self.ad)
        self.moderation_generation = max(1, int(self.moderation_generation or 1))
        self.eligibility_basis = self.eligibility_basis or "communication"

    def validate(self):
        self.rating = normalize_rating(self.rating)
        self.title = normalize_title(self.title)
        self.comment = normalize_comment(self.comment)
        if self.status not in ALL_STATUSES:
            frappe.throw("Invalid review status.")
        if self.ad and self.reviewer:
            self.review_key = review_key(reviewer=self.reviewer, ad_id=self.ad)
        self._validate_immutable_fields()
        self._validate_status_transition()

    def before_save(self):
        previous = self.get_doc_before_save()
        if previous and previous.status != self.status:
            self.reviewed_by = getattr(frappe.session, "user", None) or "Administrator"
            self.reviewed_on = now_datetime()

    def on_update(self):
        recompute_review_aggregates(ad_id=self.ad, lock_target=True)

    def after_delete(self):
        recompute_review_aggregates(ad_id=self.ad, lock_target=True)

    def _validate_immutable_fields(self):
        previous = self.get_doc_before_save()
        if not previous:
            return
        for fieldname in ("ad", "reviewer", "review_key", "eligibility_basis", "eligibility_reference"):
            if getattr(previous, fieldname, None) != getattr(self, fieldname, None):
                frappe.throw(f"{fieldname.replace('_', ' ').title()} cannot be changed.")

    def _validate_status_transition(self):
        previous = self.get_doc_before_save()
        if not previous:
            return
        allowed = _ALLOWED_TRANSITIONS.get(previous.status, {previous.status})
        if self.status not in allowed:
            frappe.throw("Invalid review status transition.")


def update_ad_rating(ad_name):
    """Backward-compatible aggregate entry point."""
    return recompute_review_aggregates(ad_id=ad_name)


def update_seller_rating_from_ad(ad_name):
    """Backward-compatible aggregate entry point."""
    return recompute_review_aggregates(ad_id=ad_name)
