# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class AOSModerationJob(Document):
    def validate(self):
        if not self.status:
            self.status = "Queued"
        if self.attempt_count is None:
            self.attempt_count = 0
        if not self.max_attempts:
            self.max_attempts = 3
        if not self.decision:
            self.decision = "pending"

        if self.target_doctype and self.target_name and not self.target_owner:
            self.target_owner = self._resolve_target_owner()

    def _resolve_target_owner(self) -> str | None:
        if self.target_doctype == "AOS Ad":
            seller = frappe.db.get_value("AOS Ad", self.target_name, "seller")
            return frappe.db.get_value("AOS Seller", seller, "user") if seller else None
        if self.target_doctype == "AOS Review":
            return frappe.db.get_value("AOS Review", self.target_name, "reviewer")
        if self.target_doctype == "AOS Short":
            return frappe.db.get_value("AOS Short", self.target_name, "owner")
        if self.target_doctype == "AOS Profile":
            return frappe.db.get_value("AOS Profile", self.target_name, "user") or self.target_name
        if self.target_doctype == "AOS Seller":
            return frappe.db.get_value("AOS Seller", self.target_name, "user")
        if self.target_doctype == "AOS Live Stream":
            return frappe.db.get_value("AOS Live Stream", self.target_name, "host")
        if self.target_doctype == "AOS Message":
            return frappe.db.get_value("AOS Message", self.target_name, "sender")
        return None
