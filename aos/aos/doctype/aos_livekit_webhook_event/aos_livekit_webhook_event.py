from __future__ import annotations

import frappe
from frappe.model.document import Document

VALID_STATUSES = {"processing", "processed", "ignored"}


class AOSLiveKitWebhookEvent(Document):
    def validate(self):
        self.event_id = str(self.event_id or "").strip()
        self.event_type = str(self.event_type or "").strip().lower()
        self.status = str(self.status or "processing").strip().lower()
        self.payload_hash = str(self.payload_hash or "").strip().lower()
        if not self.event_id or len(self.event_id) > 140:
            frappe.throw("Invalid LiveKit webhook event id.")
        if not self.event_type or len(self.event_type) > 80:
            frappe.throw("Invalid LiveKit webhook event type.")
        if self.status not in VALID_STATUSES:
            frappe.throw("Invalid LiveKit webhook status.")
        if len(self.payload_hash) != 64:
            frappe.throw("Invalid LiveKit webhook payload hash.")
