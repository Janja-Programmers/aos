# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import hashlib

import frappe
from frappe.model.document import Document

from aos.services.reviews.aggregates import apply_reaction_count_delta
from aos.services.reviews.constants import STATUS_APPROVED
from aos.services.reviews.validation import normalize_reaction


def review_reaction_name(*, review: str, user: str) -> str:
    digest = hashlib.sha256(f"{str(review).strip()}\x00{str(user).strip()}".encode("utf-8")).hexdigest()[:32]
    return f"RREACT-{digest}"


def _delta(old: str | None, new: str | None) -> tuple[int, int]:
    like = (1 if new == "Like" else 0) - (1 if old == "Like" else 0)
    dislike = (1 if new == "Dislike" else 0) - (1 if old == "Dislike" else 0)
    return like, dislike


class AOSReviewReaction(Document):
    @staticmethod
    def _authenticated_user() -> str:
        user = str(getattr(frappe.session, "user", "") or "")
        if not user or user == "Guest":
            frappe.throw("Login required", exc=frappe.PermissionError)
        return user

    def autoname(self):
        if not self.review:
            frappe.throw("Review is required.", exc=frappe.ValidationError)
        self.user = self._authenticated_user()
        self.name = review_reaction_name(review=str(self.review), user=self.user)

    def before_insert(self):
        self.user = self._authenticated_user()

    def validate(self):
        self.reaction = normalize_reaction(self.reaction)
        previous = self.get_doc_before_save()
        if previous and (previous.review != self.review or previous.user != self.user):
            frappe.throw("Review reaction ownership cannot be changed.", exc=frappe.ValidationError)
        review = frappe.db.get_value("AOS Review", self.review, ["reviewer", "status"], as_dict=True)
        if not review or review.status != STATUS_APPROVED:
            frappe.throw("You can only react to approved reviews.", exc=frappe.ValidationError)
        if review.reviewer == self.user:
            frappe.throw("You cannot react to your own review.", exc=frappe.PermissionError)

    def after_insert(self):
        like_delta, dislike_delta = _delta(None, self.reaction)
        apply_reaction_count_delta(
            review_id=self.review,
            like_delta=like_delta,
            dislike_delta=dislike_delta,
        )

    def on_update(self):
        previous = self.get_doc_before_save()
        if not previous or previous.reaction == self.reaction:
            return
        like_delta, dislike_delta = _delta(previous.reaction, self.reaction)
        apply_reaction_count_delta(
            review_id=self.review,
            like_delta=like_delta,
            dislike_delta=dislike_delta,
        )

    def on_trash(self):
        like_delta, dislike_delta = _delta(self.reaction, None)
        apply_reaction_count_delta(
            review_id=self.review,
            like_delta=like_delta,
            dislike_delta=dislike_delta,
        )


def update_review_reaction_counts(review_name):
    """Operator reconciliation entry point."""
    from aos.services.reviews.aggregates import recompute_review_reaction_counts

    return recompute_review_reaction_counts(review_id=review_name, lock_review=True)
