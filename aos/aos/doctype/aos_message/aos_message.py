# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSMessage(Document):
    def validate(self):
        self._validate_conversation()
        self._validate_sender()
        self._validate_message_content()

    def _validate_conversation(self):
        if not self.conversation:
            frappe.throw("Conversation is required")

    def _validate_sender(self):
        if not self.sender:
            frappe.throw("Sender is required")

        if self.message_type == "system":
            return

        convo = frappe.db.get_value(
            "AOS Conversation",
            self.conversation,
            ["participant_1", "participant_2"],
            as_dict=True,
        )

        if not convo:
            frappe.throw("Invalid conversation")

        if self.sender not in (convo.participant_1, convo.participant_2):
            frappe.throw("Sender must be a participant in the conversation")

    def _validate_message_content(self):
        content = (self.content or "").strip()

        if self.message_type in ("text", "mixed") and not content:
            frappe.throw("Content is required for text and mixed messages")

        if self.message_type == "media" and content:
            self.content = None

    def before_insert(self):
        self._sync_attachment_flag()

    def before_save(self):
        self._protect_status_fields()

    def _sync_attachment_flag(self):
        if not self.has_attachments:
            self.has_attachments = 0

    def _protect_status_fields(self):
        if self.is_new():
            return

        original = frappe.db.get_value(
            self.doctype,
            self.name,
            ["delivered_to_receiver_at", "read_by_receiver_at"],
            as_dict=True,
        )

        if not original:
            return

        if (
            self.delivered_to_receiver_at != original.delivered_to_receiver_at
            or self.read_by_receiver_at != original.read_by_receiver_at
        ):
            frappe.throw("Message status fields cannot be modified directly")
