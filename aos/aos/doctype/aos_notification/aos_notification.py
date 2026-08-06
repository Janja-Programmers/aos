# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class AOSNotification(Document):
    def before_insert(self):
        """
        Lightweight validation and normalization.
        """

        # Ensure required fields
        if not self.user:
            frappe.throw("User is required for notification.")

        if not self.type:
            frappe.throw("Notification type is required.")

        if not self.title:
            frappe.throw("Notification title is required.")

        if not self.body:
            frappe.throw("Notification body is required.")

        # Prevent self-notifications (actor == user)
        if self.actor and self.actor == self.user:
            frappe.throw("Cannot create notification for the same user as actor.")

        # Normalize JSON payload
        if not self.payload:
            self.payload = {}

        dedupe_key = str(getattr(self, "dedupe_key", "") or "").strip()
        self.dedupe_key = dedupe_key or None
        if dedupe_key and len(dedupe_key) > 180:
            frappe.throw("Notification dedupe key is too long.")

        # Ensure is_read default (safety)
        if self.is_read is None:
            self.is_read = 0

    def mark_as_read(self):
        """
        Mark notification as read.
        """
        if not self.is_read:
            self.db_set("is_read", 1, update_modified=False)

    def mark_as_unread(self):
        """
        Mark notification as unread.
        """
        if self.is_read:
            self.db_set("is_read", 0, update_modified=False)
