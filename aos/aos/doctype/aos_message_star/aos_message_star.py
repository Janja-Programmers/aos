# Copyright (c) 2026, Africa Online Stores and contributors
from __future__ import annotations

import frappe
from frappe.model.document import Document


class AOSMessageStar(Document):
    def validate(self):
        if not self.message or not self.conversation or not self.user:
            frappe.throw("Message, conversation and user are required")
        message = frappe.db.get_value(
            "AOS Message", self.message,
            ["conversation", "creation", "deleted_for_everyone"], as_dict=True,
        )
        if not message or message.conversation != self.conversation or int(message.deleted_for_everyone or 0):
            frappe.throw("Invalid or unavailable message")
        membership = frappe.db.get_value(
            "AOS Conversation Participant",
            {"conversation": self.conversation, "user": self.user, "status": "active"},
            ["visible_from", "cleared_before"], as_dict=True,
        )
        if not membership or message.creation < membership.visible_from or (
            membership.cleared_before and message.creation <= membership.cleared_before
        ):
            frappe.throw("Message is not visible to this user")
        hidden = frappe.db.get_value("AOS Message User State", {"message": self.message, "user": self.user}, "hidden_at")
        if hidden:
            frappe.throw("Message is not visible to this user")
        filters = {"message": self.message, "user": self.user}
        if not self.is_new():
            filters["name"] = ["!=", self.name]
        if frappe.db.exists("AOS Message Star", filters):
            frappe.throw("Message is already starred by this user")
