# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


VALID_MESSAGE_TYPES = {
    "text",
    "media",
    "ad",
    "mixed",
    "system",
}


class AOSMessage(Document):
    def validate(self):
        self._validate_conversation()
        self._validate_message_type()
        self._validate_sender()
        self._validate_message_content()
        self._validate_ad_reference()
        self._validate_reply_to_message()

    def before_insert(self):
        self._sync_defaults()

    def before_save(self):
        self._protect_system_managed_fields()

    # Validation
    def _validate_conversation(self):
        if not self.conversation:
            frappe.throw("Conversation is required")

        if not frappe.db.exists("AOS Conversation", self.conversation):
            frappe.throw("Invalid conversation")

    def _validate_message_type(self):
        if not self.message_type:
            frappe.throw("Message Type is required")

        if self.message_type not in VALID_MESSAGE_TYPES:
            frappe.throw(f"Invalid message type: {self.message_type}")

    def _validate_sender(self):
        if not self.sender:
            frappe.throw("Sender is required")

        # System messages may be created by backend jobs/services.
        # They do not have to follow participant validation.
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
        has_ad = bool(self.ad)
        has_attachments = bool(self.has_attachments)

        if self.message_type == "text":
            if not content:
                frappe.throw("Content is required for text messages")

            self.content = content
            return

        if self.message_type == "media":
            # Media-only messages should not store accidental text content.
            if content:
                self.content = None
            return

        if self.message_type == "ad":
            if not has_ad:
                frappe.throw("Ad is required for ad messages")

            # Ad-only messages may optionally have no text.
            self.content = content or None
            return

        if self.message_type == "mixed":
            # Mixed can be:
            # - text + media
            # - text + ad
            # - ad + media
            # - text + ad + media
            #
            # Attachments are inserted after the message row in the API,
            # so has_attachments may still be 0 during initial validation.
            # Therefore, content OR ad is enough here.
            if not content and not has_ad and not has_attachments:
                frappe.throw("Mixed messages require content, an ad, or attachments")

            self.content = content or None
            return

        if self.message_type == "system":
            # System messages usually need readable content for chat history.
            if not content:
                frappe.throw("Content is required for system messages")

            self.content = content
            return

    def _validate_ad_reference(self):
        if not self.ad:
            return

        if not frappe.db.exists("AOS Ad", self.ad):
            frappe.throw("Invalid ad reference")

    def _validate_reply_to_message(self):
        if not self.reply_to_message:
            return

        replied = frappe.db.get_value(
            "AOS Message",
            self.reply_to_message,
            ["name", "conversation"],
            as_dict=True,
        )

        if not replied:
            frappe.throw("Reply message not found")

        if replied.conversation != self.conversation:
            frappe.throw("You can only reply to a message in the same conversation")

        # On normal insert, self.name may not be assigned yet, but this protects
        # future updates/imports from creating a self-referencing reply.
        if self.name and replied.name == self.name:
            frappe.throw("A message cannot reply to itself")

    # Internal helpers
    def _sync_defaults(self):
        if not self.has_attachments:
            self.has_attachments = 0

        if not self.is_edited:
            self.is_edited = 0

    def _protect_system_managed_fields(self):
        if self.is_new():
            return

        protected_fields = [
            "delivered_to_receiver_at",
            "read_by_receiver_at",
            "is_edited",
            "edited_at",
            "original_content",
        ]

        original = frappe.db.get_value(
            self.doctype,
            self.name,
            protected_fields,
            as_dict=True,
        )

        if not original:
            return

        for fieldname in protected_fields:
            if getattr(self, fieldname, None) != original.get(fieldname):
                frappe.throw(f"{fieldname} cannot be modified directly")
