# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class AOSMessageTranslation(Document):
    def validate(self):
        self._validate_required_fields()
        self._normalize_fields()
        self._validate_message()
        self._validate_conversation()
        self._validate_message_belongs_to_conversation()
        self._validate_translated_by()

    # Validation
    def _validate_required_fields(self):
        if not self.message:
            frappe.throw("Message is required")

        if not self.conversation:
            frappe.throw("Conversation is required")

        if not self.source_language:
            frappe.throw("Source Language is required")

        if not self.target_language:
            frappe.throw("Target Language is required")

        if not self.original_content_hash:
            frappe.throw("Original Content Hash is required")

        if not self.translated_content:
            frappe.throw("Translated Content is required")

        if not self.translated_by:
            frappe.throw("Translated By is required")

    def _normalize_fields(self):
        self.source_language = (self.source_language or "").strip()
        self.source_language_label = (self.source_language_label or "").strip() or None

        self.target_language = (self.target_language or "").strip()
        self.target_language_label = (self.target_language_label or "").strip() or None

        self.original_content_hash = (self.original_content_hash or "").strip()
        self.translated_content = (self.translated_content or "").strip()

        self.provider = (self.provider or "").strip() or None

        # Optional field. Present if you added it to the DocType.
        if hasattr(self, "model_name"):
            self.model_name = (self.model_name or "").strip() or None

    def _validate_message(self):
        if not frappe.db.exists("AOS Message", self.message):
            frappe.throw("Invalid message")

    def _validate_conversation(self):
        if not frappe.db.exists("AOS Conversation", self.conversation):
            frappe.throw("Invalid conversation")

    def _validate_message_belongs_to_conversation(self):
        message_conversation = frappe.db.get_value(
            "AOS Message",
            self.message,
            "conversation",
        )

        if message_conversation != self.conversation:
            frappe.throw("Message does not belong to this conversation")

    def _validate_translated_by(self):
        if not frappe.db.exists("User", self.translated_by):
            frappe.throw("Invalid translated by user")
