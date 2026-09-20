from __future__ import annotations
import frappe
from frappe.model.document import Document
from aos.services.shorts.constants import EVENT_TYPES
from aos.services.shorts.policy import can_view


class AOSShortEvent(Document):
    def validate(self):
        if not self.user:
            self.user = frappe.session.user
        if self.user == "Guest":
            self.user = None
        if not self.user and not self.session_id:
            frappe.throw("User or session_id is required")
        if str(self.event_type or "") not in EVENT_TYPES:
            frappe.throw("Invalid event type")
        short = frappe.db.get_value(
            "AOS Short", self.short,
            ["name", "owner", "lifecycle_status", "processing_status", "moderation_status", "audience"],
            as_dict=True,
        )
        if not short or not can_view(short, viewer=self.user):
            frappe.throw("Short is not available")
        self.watch_ms = max(0, min(int(self.watch_ms or 0), 600000))
        self.progress_ms = max(0, min(int(self.progress_ms or 0), 600000))
