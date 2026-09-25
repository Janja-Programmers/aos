# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.reports.constants import REPORT_TARGET_SHORT, STATUS_REVIEWING
from aos.services.reports.errors import ReportError
from aos.services.reports.lifecycle import prepare_new_report, stamp_review_metadata, validate_report_lifecycle
from aos.services.reports.repository import active_report_key, find_reviewing_report, locked_previous_report
from aos.services.reports.validation import validate_existing_reason, validate_reason_for_target
from aos.utils.identifiers import new_prefixed_name


class AOSShortReport(Document):
    def autoname(self):
        self.name = new_prefixed_name("SRPT")

    def before_insert(self):
        user = str(getattr(frappe.session, "user", "") or "")
        if user not in {"", "Guest", "Administrator"}:
            self.reported_by = user
        prepare_new_report(self)

    def validate(self):
        previous = locked_previous_report(self)
        self._validate_short(previous)
        self._validate_reporter(previous)
        try:
            self.reason = (
                validate_reason_for_target(self.reason, REPORT_TARGET_SHORT)
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

    def _validate_short(self, previous):
        # Existing evidence remains reviewable after the Short is hidden/deleted.
        # Shared lifecycle validation protects the persisted relationship fields.
        if previous is not None:
            return
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
        from aos.services.shorts.policy import can_view

        viewer = str(getattr(frappe.session, "user", "") or "") or None
        if not can_view(short, viewer=viewer):
            frappe.throw("Short is not available.", exc=frappe.DoesNotExistError)
        self.short_owner = short.owner

    def _validate_reporter(self, previous):
        if previous is not None:
            return
        if not self.reported_by or self.reported_by == "Guest":
            frappe.throw("Login required to report a short.", exc=frappe.PermissionError)
        if not frappe.db.exists("User", self.reported_by):
            frappe.throw("Reporting user does not exist.", exc=frappe.DoesNotExistError)
        if self.reported_by == self.short_owner:
            frappe.throw("You cannot report your own short.", exc=frappe.PermissionError)

    def _set_active_key(self):
        self.active_key = (
            active_report_key(doctype=self.doctype, target_id=self.short, reporter=self.reported_by)
            if self.status == STATUS_REVIEWING and self.short and self.reported_by
            else None
        )

    def _prevent_duplicate_reviewing_report(self):
        if not self.short or not self.reported_by:
            return
        existing = find_reviewing_report(
            doctype=self.doctype,
            target_id=self.short,
            reporter=self.reported_by,
            exclude_name=self.name,
        )
        if existing:
            frappe.throw("A report for this short is already under review.", exc=frappe.ValidationError)
