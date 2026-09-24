# Copyright (c) 2026, Africa Online Stores and contributors

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.chat.identifiers import generate_participant_id


class AOSConversationParticipant(Document):
    def autoname(self):
        self.name = generate_participant_id()

    def before_insert(self):
        now = now_datetime()
        self.joined_at = self.joined_at or now
        self.visible_from = self.visible_from or self.joined_at

    def validate(self):
        if not self.conversation or not frappe.db.exists("AOS Conversation", self.conversation):
            frappe.throw("Invalid conversation")
        if not self.user or not frappe.db.exists("User", self.user):
            frappe.throw("Invalid conversation participant")
        if self.role not in {"owner", "admin", "member"}:
            frappe.throw("Invalid conversation role")
        if self.status not in {"active", "left", "removed"}:
            frappe.throw("Invalid conversation participant status")
        if self.status == "active":
            self.left_at = None
