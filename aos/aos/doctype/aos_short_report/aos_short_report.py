# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import hashlib

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class AOSShortReport(Document):
    def before_insert(self):
        self._set_defaults()

    def validate(self):
        self._validate_short()
        self._validate_reporter()
        self._validate_reason()
        self._set_active_key()
        self._prevent_duplicate_active_report()
        self._sync_review_fields()

    def _set_defaults(self):
        if not self.status:
            self.status = "Reviewing"

        if not self.reported_by:
            self.reported_by = frappe.session.user

    def _validate_short(self):
        if not self.short:
            frappe.throw("Short is required")

        short = frappe.db.get_value(
            "AOS Short",
            self.short,
            ["name", "owner", "status", "visibility_status"],
            as_dict=True,
        )

        if not short:
            frappe.throw("Short not found")

        if short.status != "ready" or short.visibility_status != "visible":
            frappe.throw("Short is not available")

        self.short_owner = short.owner

    def _validate_reporter(self):
        if not self.reported_by or self.reported_by == "Guest":
            frappe.throw("Login required to report a short")

        if self.reported_by == self.short_owner:
            frappe.throw("You cannot report your own short")

    def _validate_reason(self):
        if not self.reason:
            frappe.throw("Reason is required")

        reason = frappe.db.get_value(
            "AOS Report Reason",
            self.reason,
            ["name", "is_active"],
            as_dict=True,
        )

        if not reason:
            frappe.throw("Invalid report reason")

        if not int(reason.is_active or 0):
            frappe.throw("Selected report reason is inactive")

    def _set_active_key(self):
        if self.status == "Reviewing" and self.short and self.reported_by:
            material = f"{self.short}|{self.reported_by}".encode("utf-8")
            self.active_key = hashlib.sha256(material).hexdigest()
        else:
            self.active_key = None

    def _prevent_duplicate_active_report(self):
        existing = frappe.db.get_value(
            "AOS Short Report",
            {
                "short": self.short,
                "reported_by": self.reported_by,
                "status": ["!=", "Rejected"],
                "name": ["!=", self.name],
            },
            "name",
        )

        if existing:
            frappe.throw("You have already reported this short")

    def _sync_review_fields(self):
        if self.status == "Reviewing":
            return

        if not self.reviewed_by:
            self.reviewed_by = frappe.session.user

        if not self.reviewed_on:
            self.reviewed_on = now_datetime()
