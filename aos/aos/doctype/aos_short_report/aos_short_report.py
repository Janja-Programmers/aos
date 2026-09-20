# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import hashlib

import frappe
from frappe.model.document import Document

from aos.services.reports.errors import ReportError
from aos.services.reports.lifecycle import prepare_new_report, stamp_review_metadata, validate_report_lifecycle
from aos.services.reports.moderation import apply_admin_action_once
from aos.services.reports.repository import locked_previous_report
from aos.services.reports.validation import normalize_reason, validate_active_reason


class AOSShortReport(Document):
    def before_insert(self):
        user = str(getattr(frappe.session, "user", "") or "")
        if user not in {"", "Guest", "Administrator"}:
            self.reported_by = user
        prepare_new_report(self)

    def validate(self):
        previous = locked_previous_report(self)
        self._validate_short(previous)
        self._validate_reporter()
        try:
            self._validate_reason(previous)
            self._set_active_key()
            self._prevent_duplicate_active_report()
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

    def on_update(self):
        apply_admin_action_once(self, self.get_doc_before_save())

    def _validate_short(self, previous):
        if not self.short:
            frappe.throw("Short is required.", exc=frappe.ValidationError)
        short = frappe.db.get_value(
            "AOS Short",
            self.short,
            ["name", "owner", "lifecycle_status", "processing_status", "moderation_status", "audience"],
            as_dict=True,
        )
        if not short:
            frappe.throw("Short not found.", exc=frappe.DoesNotExistError)
        if previous is None:
            from aos.services.shorts.policy import can_view
            viewer = str(getattr(frappe.session, "user", "") or "") or None
            if not can_view(short, viewer=viewer):
                frappe.throw("Short is not available.", exc=frappe.DoesNotExistError)
            self.short_owner = short.owner

    def _validate_reporter(self):
        if not self.reported_by or self.reported_by == "Guest":
            frappe.throw("Login required to report a short.", exc=frappe.PermissionError)
        if self.reported_by == self.short_owner:
            frappe.throw("You cannot report your own short.", exc=frappe.PermissionError)

    def _validate_reason(self, previous):
        self.reason = normalize_reason(self.reason)
        if previous is None:
            validate_active_reason(self.reason)
        elif not frappe.db.exists("AOS Report Reason", self.reason):
            frappe.throw("Invalid report reason.", exc=frappe.ValidationError)

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
            frappe.throw("You have already reported this short.", exc=frappe.ValidationError)
