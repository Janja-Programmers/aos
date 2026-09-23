from __future__ import annotations

import frappe
from frappe.model.document import Document

VALID_ROLES = {"initiator", "participant"}
VALID_STATUSES = {"invited", "ringing", "joined", "declined", "missed", "left", "failed", "cancelled"}


class AOSCallParticipant(Document):
    def validate(self):
        if not self.call or not frappe.db.exists("AOS Call", self.call):
            frappe.throw("Call is required")
        if not self.user or not frappe.db.exists("User", self.user):
            frappe.throw("Participant account is required")
        if self.role not in VALID_ROLES:
            frappe.throw("Invalid call participant role")
        if self.status not in VALID_STATUSES:
            frappe.throw("Invalid call participant status")
        existing = frappe.db.get_value(
            "AOS Call Participant",
            {"call": self.call, "user": self.user, "name": ["!=", self.name or ""]},
            "name",
        )
        if existing:
            frappe.throw("Account is already a participant in this call")
