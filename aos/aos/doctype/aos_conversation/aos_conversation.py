# Copyright (c) 2026, Africa Online Stores and contributors

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.chat.identifiers import generate_conversation_id

VALID_CONVERSATION_TYPES = {"direct", "group"}


class AOSConversation(Document):
    def autoname(self):
        self.name = generate_conversation_id()

    def validate(self):
        kind = str(self.conversation_type or "").strip().lower()
        if kind not in VALID_CONVERSATION_TYPES:
            frappe.throw("Invalid conversation type")
        self.conversation_type = kind
        if not self.created_by or not frappe.db.exists("User", self.created_by):
            frappe.throw("Invalid conversation creator")
        if kind == "direct":
            if not str(self.direct_key or "").strip():
                frappe.throw("Direct conversation key is required")
            self.title = None
            self.avatar_media = None
        else:
            self.direct_key = None
            title = str(self.title or "").strip()
            if not title or len(title) > 140:
                frappe.throw("Group title is required and must not exceed 140 characters")
            self.title = title

    def before_save(self):
        if self.is_new():
            return
        original = frappe.db.get_value(self.doctype, self.name, ["conversation_type", "direct_key", "created_by"], as_dict=True)
        if not original:
            return
        for fieldname in ("conversation_type", "direct_key", "created_by"):
            if getattr(self, fieldname, None) != original.get(fieldname):
                frappe.throw(f"{fieldname} cannot be modified directly")
