# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.reports.errors import ReportError
from aos.services.reports.lifecycle import prepare_new_report, stamp_review_metadata, validate_report_lifecycle
from aos.services.reports.repository import locked_previous_report
from aos.services.reports.validation import normalize_reason, validate_active_reason
from aos.services.reviews.constants import STATUS_APPROVED


class AOSReviewReport(Document):
    def before_insert(self):
        user = str(getattr(frappe.session, "user", "") or "")
        if user not in {"", "Guest", "Administrator"}:
            self.reported_by = user
        prepare_new_report(self)

    def validate(self):
        previous = locked_previous_report(self)
        try:
            self._validate_reason(previous)
        except ReportError as exc:
            frappe.throw(str(exc), frappe.ValidationError)
        review = frappe.db.get_value("AOS Review", self.review, ["reviewer", "status"], as_dict=True)
        if not review:
            frappe.throw("Review not found.", exc=frappe.DoesNotExistError)
        if previous is None and review.status != STATUS_APPROVED:
            frappe.throw("Review not found.", exc=frappe.DoesNotExistError)
        if review.reviewer == self.reported_by:
            frappe.throw("You cannot report your own review.", exc=frappe.PermissionError)
        self._validate_duplicate()
        try:
            validate_report_lifecycle(self, previous, actor=str(getattr(frappe.session, "user", "") or ""))
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

    def _validate_reason(self, previous):
        self.reason = normalize_reason(self.reason)
        if previous is None:
            validate_active_reason(self.reason)
        elif not frappe.db.exists("AOS Report Reason", self.reason):
            frappe.throw("Invalid report reason.", exc=frappe.ValidationError)

    def _validate_duplicate(self):
        if not self.review or not self.reported_by:
            return
        if frappe.db.exists(
            "AOS Review Report",
            {"review": self.review, "reported_by": self.reported_by, "name": ["!=", self.name]},
        ):
            frappe.throw("You have already reported this review.", exc=frappe.ValidationError)
