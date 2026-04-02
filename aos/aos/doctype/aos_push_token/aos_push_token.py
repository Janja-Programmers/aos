# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class AOSPushToken(Document):
    def before_insert(self):
        """
        Ensure clean insert and prevent duplicates.
        """

        if not self.user:
            frappe.throw("User is required.")

        if not self.token:
            frappe.throw("FCM token is required.")

        if not self.device_type:
            frappe.throw("Device type is required.")

        # Normalize
        if self.is_active is None:
            self.is_active = 1

        if not self.last_used_at:
            self.last_used_at = now_datetime()

        # Deduplicate: if token already exists, update instead of insert
        existing = frappe.db.get_value(
            "AOS Push Token",
            {"token": self.token},
            ["name", "user"],
            as_dict=True,
        )

        if existing:
            # Update existing record instead of inserting duplicate
            frappe.db.set_value(
                "AOS Push Token",
                existing.name,
                {
                    "user": self.user,
                    "device_type": self.device_type,
                    "device_id": self.device_id,
                    "is_active": 1,
                    "last_used_at": self.last_used_at,
                },
                update_modified=False,
            )

            # Prevent new insert
            frappe.throw("Token already exists. Updated existing record.")

    def before_save(self):
        """
        Update usage timestamp on every save.
        """
        self.last_used_at = now_datetime()

    def deactivate(self):
        """
        Deactivate this token (e.g., logout).
        """
        if self.is_active:
            self.db_set("is_active", 0, update_modified=False)

    def activate(self):
        """
        Reactivate token.
        """
        if not self.is_active:
            self.db_set("is_active", 1, update_modified=False)
