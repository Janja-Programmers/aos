# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import re

import frappe
from frappe.model.document import Document

from aos.services.reports.constants import REPORT_REASON_TARGETS
from aos.services.reports.validation import clean_text

_REASON_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class AOSReportReason(Document):
    def validate(self):
        self.reason_id = clean_text(
            self.reason_id, field="reason_id", max_length=64, required=True
        ).lower()
        if not _REASON_ID_RE.fullmatch(self.reason_id):
            frappe.throw(
                "Reason ID must use lowercase letters, numbers, and underscores.",
                exc=frappe.ValidationError,
            )
        self.label = clean_text(
            self.label, field="label", max_length=140, required=True, reject_html=True
        )
        self.description = clean_text(
            self.description,
            field="description",
            max_length=500,
            multiline=True,
            reject_html=True,
        )
        self.icon_key = clean_text(self.icon_key, field="icon_key", max_length=80)
        try:
            self.sort_order = int(self.sort_order or 0)
        except (TypeError, ValueError):
            frappe.throw("Invalid report reason sort order.", exc=frappe.ValidationError)
        try:
            self.is_enabled = 1 if int(self.is_enabled or 0) else 0
        except (TypeError, ValueError):
            frappe.throw("Invalid report reason state.", exc=frappe.ValidationError)

        targets = [str(row.target_type or "").strip() for row in (self.allowed_targets or [])]
        if not targets:
            frappe.throw("At least one allowed report target is required.", exc=frappe.ValidationError)
        if len(targets) != len(set(targets)):
            frappe.throw("Duplicate report target classification is not allowed.", exc=frappe.ValidationError)
        unsupported = sorted(set(targets) - REPORT_REASON_TARGETS)
        if unsupported:
            frappe.throw("Invalid report target classification.", exc=frappe.ValidationError)

        if not self.is_new():
            previous = self.get_doc_before_save()
            if previous and str(previous.reason_id or "") != str(self.reason_id or ""):
                frappe.throw(
                    "Report reason identity cannot be changed after creation.",
                    exc=frappe.ValidationError,
                )
