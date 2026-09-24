# Copyright (c) 2026, Africa Online Stores and contributors

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.chat.identifiers import generate_lock_credential_id


class AOSChatLockCredential(Document):
    def autoname(self):
        self.name = generate_lock_credential_id()

    def validate(self):
        if not self.user or not frappe.db.exists("User", self.user):
            frappe.throw("Invalid chat-lock user")
        if not str(self.secret_hash or "").strip():
            frappe.throw("Chat-lock secret hash is required")
        self.secret_version = max(1, int(self.secret_version or 1))
        self.failed_attempts = max(0, int(self.failed_attempts or 0))
