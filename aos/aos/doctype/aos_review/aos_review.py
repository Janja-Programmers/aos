# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.reviews.aggregates import apply_review_aggregate_delta
from aos.services.reviews.constants import (
    ALL_STATUSES,
    STATUS_APPROVED,
    STATUS_HIDDEN,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_WITHDRAWN,
)
from aos.services.reviews.eligibility import review_key
from aos.services.reviews.ids import ensure_review_public_id
from aos.services.reviews.validation import normalize_comment, normalize_images, normalize_rating, normalize_title
from aos.utils.doctype_permissions import has_doctype_permission
from aos.utils.identifiers import new_prefixed_name

_ACTION_TRANSITIONS = {
    "owner_edit": {
        STATUS_PENDING: STATUS_PENDING,
        STATUS_APPROVED: STATUS_PENDING,
        STATUS_REJECTED: STATUS_PENDING,
    },
    "owner_withdraw": {
        STATUS_PENDING: STATUS_WITHDRAWN,
        STATUS_APPROVED: STATUS_WITHDRAWN,
        STATUS_REJECTED: STATUS_WITHDRAWN,
    },
    "moderation_allow": {
        STATUS_PENDING: STATUS_APPROVED,
    },
    "moderation_reject": {
        STATUS_PENDING: STATUS_REJECTED,
    },
    "moderation_review": {
        STATUS_PENDING: STATUS_PENDING,
    },
    "manual_approve": {
        STATUS_PENDING: STATUS_APPROVED,
        STATUS_REJECTED: STATUS_APPROVED,
        STATUS_HIDDEN: STATUS_APPROVED,
    },
    "manual_reject": {
        STATUS_PENDING: STATUS_REJECTED,
        STATUS_APPROVED: STATUS_REJECTED,
        STATUS_HIDDEN: STATUS_REJECTED,
    },
    "admin_hide": {
        STATUS_PENDING: STATUS_HIDDEN,
        STATUS_APPROVED: STATUS_HIDDEN,
        STATUS_REJECTED: STATUS_HIDDEN,
    },
}
_DECISION_ACTIONS = frozenset({"moderation_allow", "moderation_reject", "moderation_review", "manual_approve", "manual_reject", "admin_hide"})
_CONTENT_FIELDS = ("rating", "title", "comment", "review_images")
_IMMUTABLE_FIELDS = ("public_id", "ad", "reviewer", "review_key", "eligibility_basis", "eligibility_reference")


class AOSReview(Document):
    """Fail-closed Reviews persistence boundary.

    Public services own request validation/orchestration. This controller owns
    immutable identity, lifecycle authorization, canonical content validation,
    and durable aggregate projection updates for every write path including Desk.
    """

    def autoname(self):
        self.name = new_prefixed_name("REVIEW")

    def before_insert(self):
        user = str(getattr(frappe.session, "user", "") or "")
        if not user or user == "Guest":
            frappe.throw("Login required", exc=frappe.PermissionError)
        if not has_doctype_permission(user=user, doctype=self.doctype, ptype="create"):
            self.reviewer = user
        if not self.reviewer:
            frappe.throw("Reviewer is required.", exc=frappe.ValidationError)
        ensure_review_public_id(self)
        self.status = STATUS_PENDING
        self.review_key = review_key(reviewer=self.reviewer, ad_id=self.ad)
        self.moderation_generation = max(1, int(self.moderation_generation or 1))
        self.edit_count = max(0, int(self.edit_count or 0))
        self.eligibility_basis = self.eligibility_basis or "communication"
        self.review_notes = ""

    def validate(self):
        self.rating = normalize_rating(self.rating)
        self.title = normalize_title(self.title)
        self.comment = normalize_comment(self.comment)
        if self.status not in ALL_STATUSES:
            frappe.throw("Invalid review status.", exc=frappe.ValidationError)
        if self.ad and self.reviewer:
            self.review_key = review_key(reviewer=self.reviewer, ad_id=self.ad)
        self._validate_media_rows()
        self._validate_immutable_fields()
        self._validate_content_action()
        self._validate_status_transition()

    def before_save(self):
        action = self._action()
        previous = self.get_doc_before_save()
        if previous and action in _DECISION_ACTIONS and (previous.status != self.status or action == "moderation_review"):
            reviewer = str(self.flags.get("aos_reviewed_by") or "").strip()
            self.reviewed_by = reviewer or None
            self.reviewed_on = now_datetime()

    def after_insert(self):
        apply_review_aggregate_delta(
            ad_id=self.ad,
            old_status=None,
            old_rating=None,
            new_status=self.status,
            new_rating=self.rating,
        )

    def on_update(self):
        previous = self.get_doc_before_save()
        if not previous:
            return
        apply_review_aggregate_delta(
            ad_id=self.ad,
            old_status=previous.status,
            old_rating=previous.rating,
            new_status=self.status,
            new_rating=self.rating,
        )

    def on_trash(self):
        apply_review_aggregate_delta(
            ad_id=self.ad,
            old_status=self.status,
            old_rating=self.rating,
            new_status=None,
            new_rating=None,
        )

    def _action(self) -> str:
        return str(self.flags.get("aos_review_action") or "").strip().lower()

    def _validate_media_rows(self) -> None:
        media_ids = [str(row.media or "").strip() for row in (self.review_images or [])]
        normalized = normalize_images(media_ids)
        if normalized != media_ids:
            frappe.throw("Invalid review media.", exc=frappe.ValidationError)

    def _validate_immutable_fields(self) -> None:
        previous = self.get_doc_before_save()
        if not previous:
            return
        for fieldname in _IMMUTABLE_FIELDS:
            if getattr(previous, fieldname, None) != getattr(self, fieldname, None):
                frappe.throw(f"{fieldname.replace('_', ' ').title()} cannot be changed.", exc=frappe.ValidationError)

    def _validate_content_action(self) -> None:
        previous = self.get_doc_before_save()
        if not previous:
            return
        if not any(self.has_value_changed(fieldname) for fieldname in _CONTENT_FIELDS):
            return
        if self._action() != "owner_edit":
            frappe.throw("Review content requires the owner edit action.", exc=frappe.PermissionError)

    def _validate_status_transition(self) -> None:
        previous = self.get_doc_before_save()
        if not previous:
            if self.status != STATUS_PENDING:
                frappe.throw("New reviews must enter Pending state.", exc=frappe.ValidationError)
            return
        if previous.status == self.status:
            return
        action = self._action()
        target = _ACTION_TRANSITIONS.get(action, {}).get(previous.status)
        if target != self.status:
            frappe.throw("Invalid review status transition.", exc=frappe.ValidationError)


def update_ad_rating(ad_name):
    """Reconciliation entry point for operator tooling."""
    from aos.services.reviews.aggregates import recompute_review_aggregates

    return recompute_review_aggregates(ad_id=ad_name, lock_target=True)


def update_seller_rating_from_ad(ad_name):
    """Reconciliation entry point for operator tooling."""
    from aos.services.reviews.aggregates import recompute_review_aggregates

    return recompute_review_aggregates(ad_id=ad_name, lock_target=True)
