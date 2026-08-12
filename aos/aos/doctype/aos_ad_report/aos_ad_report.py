from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.reports.errors import ReportError
from aos.services.reports.lifecycle import prepare_new_report, stamp_review_metadata, validate_report_lifecycle
from aos.services.reports.moderation import apply_admin_action_once
from aos.services.reports.repository import locked_previous_report
from aos.services.reports.validation import normalize_reason, validate_active_reason


class AOSAdReport(Document):
    def before_insert(self):
        user = str(getattr(frappe.session, "user", "") or "")
        if user not in {"", "Guest", "Administrator"}:
            self.reported_by = user
        ad = frappe.db.get_value("AOS Ad", self.ad, ["seller", "status"], as_dict=True)
        if not ad or ad.status != "Active":
            frappe.throw("Ad not found.", exc=frappe.DoesNotExistError)
        seller = frappe.db.get_value("AOS Seller", ad.seller, ["name", "status"], as_dict=True)
        if not seller or seller.status != "Active":
            frappe.throw("Ad not found.", exc=frappe.DoesNotExistError)
        self.seller = ad.seller
        prepare_new_report(self)

    def validate(self):
        previous = locked_previous_report(self)
        try:
            self._validate_reason(previous)
            self._validate_duplicate()
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

    def after_insert(self):
        self._recompute_ad_total_reports()

    def on_update(self):
        apply_admin_action_once(self, self.get_doc_before_save())
        self._recompute_if_status_changed()

    def on_trash(self):
        self._recompute_ad_total_reports()

    def _validate_reason(self, previous):
        self.reason = normalize_reason(self.reason)
        if previous is None:
            validate_active_reason(self.reason)
        elif not frappe.db.exists("AOS Report Reason", self.reason):
            frappe.throw("Invalid report reason.", exc=frappe.ValidationError)

    def _validate_duplicate(self):
        if not self.ad or not self.reported_by:
            return
        if frappe.db.exists(
            "AOS Ad Report",
            {"ad": self.ad, "reported_by": self.reported_by, "name": ["!=", self.name]},
        ):
            frappe.throw("You have already reported this ad.", exc=frappe.ValidationError)

    def _recompute_ad_total_reports(self):
        if not self.ad:
            return
        total = frappe.db.count("AOS Ad Report", {"ad": self.ad, "status": ["!=", "Rejected"]})
        frappe.db.set_value("AOS Ad", self.ad, "total_reports", int(total or 0), update_modified=False)

    def _recompute_if_status_changed(self):
        previous = self.get_doc_before_save()
        if not previous or previous.status != self.status:
            self._recompute_ad_total_reports()
