# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.reports.constants import REPORT_TARGET_USER, STATUS_REVIEWING
from aos.services.reports.errors import ReportError
from aos.services.reports.lifecycle import prepare_new_report, stamp_review_metadata, validate_report_lifecycle
from aos.services.reports.policy import require_reportable_user
from aos.services.reports.repository import active_report_key, find_reviewing_report, locked_previous_report
from aos.services.reports.validation import validate_existing_reason, validate_reason_for_target
from aos.utils.identifiers import new_prefixed_name


class AOSUserReport(Document):
    def autoname(self):
        self.name = new_prefixed_name("URPT")

    def before_insert(self):
        session_user = str(getattr(frappe.session, "user", "") or "")
        if session_user not in {"", "Guest", "Administrator"}:
            self.reported_by = session_user
        prepare_new_report(self)

    def validate(self):
        previous = locked_previous_report(self)
        self._validate_users(previous)
        try:
            self.reason = (
                validate_reason_for_target(self.reason, REPORT_TARGET_USER)
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

    def _validate_users(self, previous):
        # Historical evidence remains reviewable if either account later becomes
        # unavailable; lifecycle protection below prevents relationship edits.
        if previous is not None:
            return
        if not self.reported_user or not frappe.db.exists("User", self.reported_user):
            frappe.throw("Reported user does not exist.", exc=frappe.DoesNotExistError)
        if not self.reported_by or not frappe.db.exists("User", self.reported_by):
            frappe.throw("Reporting user does not exist.", exc=frappe.DoesNotExistError)
        try:
            require_reportable_user(target_user=self.reported_user, reporter=self.reported_by)
        except ReportError as exc:
            frappe.throw(str(exc), frappe.ValidationError)

    def _set_active_key(self):
        self.active_key = (
            active_report_key(
                doctype=self.doctype,
                target_id=self.reported_user,
                reporter=self.reported_by,
            )
            if self.status == STATUS_REVIEWING and self.reported_user and self.reported_by
            else None
        )

    def _prevent_duplicate_reviewing_report(self):
        if not self.reported_user or not self.reported_by:
            return
        existing = find_reviewing_report(
            doctype=self.doctype,
            target_id=self.reported_user,
            reporter=self.reported_by,
            exclude_name=self.name,
        )
        if existing:
            frappe.throw("A report for this account is already under review.", exc=frappe.ValidationError)
