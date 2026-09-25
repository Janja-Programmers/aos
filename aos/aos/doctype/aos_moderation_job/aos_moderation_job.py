# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.moderation_contract import validate_moderation_target


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

        try:
            self.content_kind, self.target_doctype = validate_moderation_target(
                content_kind=self.content_kind,
                target_doctype=self.target_doctype,
            )
        except ValueError as exc:
            frappe.throw(str(exc), exc=frappe.ValidationError)

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
        return None
