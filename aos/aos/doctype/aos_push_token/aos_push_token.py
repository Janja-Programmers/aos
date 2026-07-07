# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import hashlib
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


def get_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AOSPushToken(Document):
    def before_insert(self):
        """
        Validate and normalize before insert.
        """

        if not self.user:
            frappe.throw("User is required.")

        if not self.token:
            frappe.throw("FCM token is required.")

        if not self.device_type:
            frappe.throw("Device type is required.")

        # Generate token hash
        self.token_hash = get_token_hash(self.token)

        # Normalize defaults
        if self.is_active is None:
            self.is_active = 1

        if not self.last_used_at:
            self.last_used_at = now_datetime()

        self._sync_active_device_key()

    def before_save(self):
        """
        Ensure hash consistency + update timestamp.
        """
        if self.token:
            self.token_hash = get_token_hash(self.token)

        self.last_used_at = now_datetime()
        self._sync_active_device_key()

    def _sync_active_device_key(self):
        """Populate DB-enforced active device uniqueness key.

        Only active rows with a non-empty device_id receive a key. Multiple
        inactive rows, or rows without device_id, are allowed.
        """
        device_id = str(self.device_id or "").strip()

        if bool(self.is_active) and self.user and device_id:
            self.active_device_key = f"{self.user}|{device_id}"
            return

        self.active_device_key = None

    def deactivate(self):
        """
        Deactivate this token (e.g., logout).
        """
        if self.is_active:
            self.db_set("is_active", 0, update_modified=False)
            self.db_set("active_device_key", None, update_modified=False)

    def activate(self):
        """
        Reactivate token.
        """
        if not self.is_active:
            self.is_active = 1
            self._sync_active_device_key()
            self.db_set("is_active", 1, update_modified=False)
            self.db_set("active_device_key", self.active_device_key, update_modified=False)
