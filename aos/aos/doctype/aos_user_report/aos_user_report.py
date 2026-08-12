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
from aos.services.reports.policy import require_reportable_user
from aos.services.reports.validation import normalize_reason, validate_active_reason


class AOSUserReport(Document):
    def before_insert(self):
        session_user = str(getattr(frappe.session, "user", "") or "")
        if session_user not in {"", "Guest", "Administrator"}:
            self.reported_by = session_user
        prepare_new_report(self)

    def validate(self):
        previous = locked_previous_report(self)
        self._validate_users()
        try:
            self._validate_reason(previous)
            if previous is None:
                require_reportable_user(target_user=self.reported_user, reporter=self.reported_by)
            self._set_active_key()
            self._prevent_duplicate_active_reports()
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

    def _validate_users(self):
        if not self.reported_user:
            frappe.throw("Reported user is required.", exc=frappe.ValidationError)
        if not self.reported_by:
            frappe.throw("Reported by is required.", exc=frappe.ValidationError)
        if self.reported_user == self.reported_by:
            frappe.throw("You cannot report yourself.", exc=frappe.ValidationError)
        if not frappe.db.exists("User", self.reported_user):
            frappe.throw("Reported user does not exist.", exc=frappe.DoesNotExistError)
        if not frappe.db.exists("User", self.reported_by):
            frappe.throw("Reporting user does not exist.", exc=frappe.DoesNotExistError)

    def _validate_reason(self, previous):
        self.reason = normalize_reason(self.reason)
        if previous is None:
            validate_active_reason(self.reason)
        elif not frappe.db.exists("AOS Report Reason", self.reason):
            frappe.throw("Invalid report reason.", exc=frappe.ValidationError)

    def _set_active_key(self):
        if self.status != "Rejected" and self.reported_user and self.reported_by:
            material = f"{self.reported_user}\x1f{self.reported_by}".encode("utf-8")
            self.active_key = hashlib.sha256(material).hexdigest()
        else:
            self.active_key = None

    def _prevent_duplicate_active_reports(self):
        if not self.reported_user or not self.reported_by:
            return
        exists = frappe.db.exists(
            "AOS User Report",
            {
                "reported_user": self.reported_user,
                "reported_by": self.reported_by,
                "status": ["!=", "Rejected"],
                "name": ["!=", self.name],
            },
        )
        if exists:
            frappe.throw("You have already reported this user.", exc=frappe.ValidationError)
