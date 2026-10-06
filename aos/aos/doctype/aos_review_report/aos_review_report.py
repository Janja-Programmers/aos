# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.reports.constants import REPORT_TARGET_REVIEW, STATUS_REVIEWING
from aos.services.reports.errors import ReportError
from aos.services.reports.lifecycle import prepare_new_report, stamp_review_metadata, validate_report_lifecycle
from aos.services.reports.repository import active_report_key, find_reviewing_report, locked_previous_report
from aos.services.reports.validation import validate_existing_reason, validate_reason_for_target
from aos.services.reviews.constants import STATUS_APPROVED
from aos.services.reviews.errors import ReviewNotFoundError
from aos.services.reviews.ids import review_public_id
from aos.services.reviews.service import ReviewService
from aos.utils.identifiers import new_prefixed_name


class AOSReviewReport(Document):
    def autoname(self):
        self.name = new_prefixed_name("RREPORT")

    def before_insert(self):
        user = str(getattr(frappe.session, "user", "") or "")
        if user not in {"", "Guest", "Administrator"}:
            self.reported_by = user
        prepare_new_report(self)

    def validate(self):
        previous = locked_previous_report(self)
        self._validate_review(previous)
        self._validate_reporter(previous)
        try:
            self.reason = (
                validate_reason_for_target(self.reason, REPORT_TARGET_REVIEW)
                if previous is None
                else validate_existing_reason(self.reason)
            )
            self._set_active_key()
            self._prevent_duplicate_reviewing_report()
            validate_report_lifecycle(
                self,
                previous,
                actor=str(getattr(frappe.session, "user", "") or ""),
            )
        except ReportError as exc:
            frappe.throw(str(exc), frappe.ValidationError)

    def before_save(self):
        try:
            stamp_review_metadata(
                self,
                locked_previous_report(self),
                actor=str(getattr(frappe.session, "user", "") or ""),
            )
        except ReportError as exc:
            frappe.throw(str(exc), frappe.ValidationError)

    def _validate_review(self, previous):
        if previous is not None:
            return
        if not self.review:
            frappe.throw("Review is required.", exc=frappe.ValidationError)
        review = frappe.db.get_value(
            "AOS Review", self.review, ["name", "public_id", "reviewer", "status"], as_dict=True
        )
        if not review or str(review.status or "") != STATUS_APPROVED:
            frappe.throw("Review not found.", exc=frappe.DoesNotExistError)
        try:
            ReviewService().get(payload={"review_id": review_public_id(review.name)}, viewer=self.reported_by)
        except ReviewNotFoundError as exc:
            frappe.throw("Review not found.", exc=frappe.DoesNotExistError)
        self.review_owner = review.reviewer

    def _validate_reporter(self, previous):
        if previous is not None:
            return
        if not self.reported_by or self.reported_by == "Guest":
            frappe.throw("Login required to report a review.", exc=frappe.PermissionError)
        if not frappe.db.exists("User", self.reported_by):
            frappe.throw("Reporting user does not exist.", exc=frappe.DoesNotExistError)
        if self.reported_by == self.review_owner:
            frappe.throw("You cannot report your own review.", exc=frappe.PermissionError)

    def _set_active_key(self):
        self.active_key = (
            active_report_key(doctype=self.doctype, target_id=self.review, reporter=self.reported_by)
            if self.status == STATUS_REVIEWING and self.review and self.reported_by
            else None
        )

    def _prevent_duplicate_reviewing_report(self):
        if not self.review or not self.reported_by:
            return
        existing = find_reviewing_report(
            doctype=self.doctype,
            target_id=self.review,
            reporter=self.reported_by,
            exclude_name=self.name,
        )
        if existing:
            frappe.throw("A report for this review is already under review.", exc=frappe.ValidationError)
