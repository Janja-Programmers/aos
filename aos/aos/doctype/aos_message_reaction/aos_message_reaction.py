# Copyright (c) 2026, Africa Online Stores and contributors
from __future__ import annotations

import frappe
from frappe.model.document import Document

MAX_EMOJI_LENGTH = 16


class AOSMessageReaction(Document):
    def validate(self):
        if not self.message or not self.conversation or not self.user or not self.emoji:
            frappe.throw("Message, conversation, user and emoji are required")
        self.emoji = str(self.emoji).strip()
        if not self.emoji or len(self.emoji) > MAX_EMOJI_LENGTH:
            frappe.throw("Invalid emoji")
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
        if frappe.db.exists("AOS Message Reaction", filters):
            frappe.throw("Message already has a reaction by this user")
