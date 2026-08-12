# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.reports.validation import clean_text


class AOSReportReason(Document):
    def validate(self):
        self.title = clean_text(self.title, field="title", max_length=140, required=True)
        self.icon_key = clean_text(self.icon_key, field="icon_key", max_length=80)
        try:
            self.sort_order = int(self.sort_order or 0)
        except (TypeError, ValueError):
            self.sort_order = 0
        try:
            self.is_active = 1 if int(self.is_active or 0) else 0
        except (TypeError, ValueError):
            frappe.throw("Invalid report reason state.", exc=frappe.ValidationError)

        if not self.is_new():
            previous = self.get_doc_before_save()
            if previous and str(previous.title or "").strip() != str(self.title or "").strip():
                frappe.throw(
                    "Report reason titles cannot be changed after creation. Deactivate this reason and create a new one.",
                    exc=frappe.ValidationError,
                )
