# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.reviews.constants import STATUS_APPROVED
from aos.services.reviews.validation import normalize_report_details, normalize_report_reason

_MODERATOR_ROLES = frozenset({"System Manager", "AOS Moderator"})


class AOSReviewReport(Document):
	def before_insert(self):
		user = str(getattr(frappe.session, "user", "") or "")
		if user not in {"", "Guest", "Administrator"}:
			self.reported_by = user
		self.status = "Reviewing"

	def validate(self):
		self.reason = normalize_report_reason(self.reason)
		self.details = normalize_report_details(self.details)
		if not frappe.db.exists("AOS Report Reason", {"name": self.reason, "is_active": 1}):
			frappe.throw("Invalid report reason.", exc=frappe.ValidationError)
		review = frappe.db.get_value(
			"AOS Review",
			self.review,
			["reviewer", "status"],
			as_dict=True,
		)
		if not review or review.status != STATUS_APPROVED:
			frappe.throw("Review not found.", exc=frappe.DoesNotExistError)
		if review.reviewer == self.reported_by:
			frappe.throw("You cannot report your own review.", exc=frappe.PermissionError)
		self._validate_duplicate()
		self._validate_moderator_change()

	def before_save(self):
		previous = self.get_doc_before_save()
		if previous and previous.status != self.status:
			self.reviewed_by = frappe.session.user
			self.reviewed_on = now_datetime()

	def _validate_duplicate(self):
		if not self.review or not self.reported_by:
			return
		if frappe.db.exists(
			"AOS Review Report",
			{
				"review": self.review,
				"reported_by": self.reported_by,
				"name": ["!=", self.name],
			},
		):
			frappe.throw("You have already reported this review.", exc=frappe.ValidationError)

	def _validate_moderator_change(self):
		if self.is_new():
			return
		previous = self.get_doc_before_save()
		if not previous or previous.status == self.status:
			return
		if not set(frappe.get_roles(frappe.session.user)).intersection(_MODERATOR_ROLES):
			frappe.throw("Moderator permission is required.", exc=frappe.PermissionError)
