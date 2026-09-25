from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.reports.constants import REPORT_TARGET_AD, STATUS_REVIEWING
from aos.services.reports.errors import ReportError
from aos.services.reports.lifecycle import prepare_new_report, stamp_review_metadata, validate_report_lifecycle
from aos.services.reports.repository import active_report_key, find_reviewing_report, locked_previous_report
from aos.services.reports.validation import validate_existing_reason, validate_reason_for_target
from aos.utils.identifiers import new_prefixed_name


class AOSAdReport(Document):
    def autoname(self):
        self.name = new_prefixed_name("ARPT")

    def before_insert(self):
        user = str(getattr(frappe.session, "user", "") or "")
        if user not in {"", "Guest", "Administrator"}:
            self.reported_by = user
        ad = frappe.db.get_value("AOS Ad", self.ad, ["seller", "status"], as_dict=True)
        if not ad:
            frappe.throw("Ad not found.", exc=frappe.DoesNotExistError)
        self.seller = ad.seller
        prepare_new_report(self)

    def validate(self):
        previous = locked_previous_report(self)
        self._validate_target(previous)
        try:
            self.reason = (
                validate_reason_for_target(self.reason, REPORT_TARGET_AD)
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

    def after_insert(self):
        self._recompute_ad_total_reports()

    def on_update(self):
        self._recompute_if_status_changed()

    def on_trash(self):
        self._recompute_ad_total_reports()

    def _validate_target(self, previous):
        # Existing evidence must remain reviewable if the Ad or reporter later
        # becomes unavailable. Shared lifecycle validation protects all links.
        if previous is not None:
            return
        ad = frappe.db.get_value("AOS Ad", self.ad, ["seller"], as_dict=True)
        if not ad:
            frappe.throw("Ad not found.", exc=frappe.DoesNotExistError)
        self.seller = ad.seller
        if not self.reported_by or not frappe.db.exists("User", self.reported_by):
            frappe.throw("Reporting user does not exist.", exc=frappe.DoesNotExistError)

    def _set_active_key(self):
        self.active_key = (
            active_report_key(doctype=self.doctype, target_id=self.ad, reporter=self.reported_by)
            if self.status == STATUS_REVIEWING and self.ad and self.reported_by
            else None
        )

    def _prevent_duplicate_reviewing_report(self):
        if not self.ad or not self.reported_by:
            return
        existing = find_reviewing_report(
            doctype=self.doctype,
            target_id=self.ad,
            reporter=self.reported_by,
            exclude_name=self.name,
        )
        if existing:
            frappe.throw("A report for this ad is already under review.", exc=frappe.ValidationError)

    def _recompute_ad_total_reports(self):
        if not self.ad:
            return
        total = frappe.db.count("AOS Ad Report", {"ad": self.ad, "status": ["!=", "Rejected"]})
        frappe.db.set_value("AOS Ad", self.ad, "total_reports", int(total or 0), update_modified=False)

    def _recompute_if_status_changed(self):
        previous = self.get_doc_before_save()
        if not previous or previous.status != self.status:
            self._recompute_ad_total_reports()
