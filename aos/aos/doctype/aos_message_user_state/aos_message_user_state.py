# Copyright (c) 2026, Africa Online Stores and contributors

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.chat.identifiers import generate_message_state_id


class AOSMessageUserState(Document):
    def autoname(self):
        self.name = generate_message_state_id()

    def validate(self):
        if not self.message or not frappe.db.exists("AOS Message", self.message):
            frappe.throw("Invalid message")
        if not self.conversation or not frappe.db.exists("AOS Conversation", self.conversation):
            frappe.throw("Invalid conversation")
        if not self.user or not frappe.db.exists("User", self.user):
            frappe.throw("Invalid user")
        message_conversation = frappe.db.get_value("AOS Message", self.message, "conversation")
        if message_conversation != self.conversation:
            frappe.throw("Message and conversation do not match")
