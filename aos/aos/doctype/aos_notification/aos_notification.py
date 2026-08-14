# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.notifications.contracts import (
    MAX_NOTIFICATION_BODY_LENGTH,
    MAX_NOTIFICATION_DEDUPE_KEY_LENGTH,
    MAX_NOTIFICATION_TITLE_LENGTH,
    NotificationContractError,
    contract_for,
    validate_persistent_payload,
)


class AOSNotification(Document):
    def before_insert(self):
        self._validate_and_normalize()

    def validate(self):
        self._validate_and_normalize()

    def _validate_and_normalize(self):
        user = str(self.user or "").strip()
        actor = str(self.actor or "").strip() or None
        notification_type = str(self.type or "").strip()
        title = str(self.title or "").strip()
        body = str(self.body or "").strip()
        dedupe_key = str(getattr(self, "dedupe_key", "") or "").strip() or None

        if not user:
            frappe.throw("User is required for notification.")
        if not frappe.db.exists("User", user):
            frappe.throw("Notification user does not exist.")
        if actor and not frappe.db.exists("User", actor):
            frappe.throw("Notification actor does not exist.")
        if actor and actor == user:
            frappe.throw("Cannot create notification for the same user as actor.")
        if not title or len(title) > MAX_NOTIFICATION_TITLE_LENGTH:
            frappe.throw("Invalid notification title.")
        if not body or len(body) > MAX_NOTIFICATION_BODY_LENGTH:
            frappe.throw("Invalid notification body.")
        if dedupe_key and len(dedupe_key) > MAX_NOTIFICATION_DEDUPE_KEY_LENGTH:
            frappe.throw("Notification dedupe key is too long.")

        try:
            contract_for(notification_type)
            payload = validate_persistent_payload(notification_type, self.payload or {})
        except NotificationContractError as exc:
            frappe.throw(str(exc))

        self.user = user
        self.actor = actor
        self.type = notification_type
        self.title = title
        self.body = body
        self.payload = payload
        self.dedupe_key = dedupe_key
        if self.is_read is None:
            self.is_read = 0
        self.is_read = 1 if bool(int(self.is_read or 0)) else 0

    def mark_as_read(self):
        if not self.is_read:
            self.db_set("is_read", 1, update_modified=False)

    def mark_as_unread(self):
        if self.is_read:
            self.db_set("is_read", 0, update_modified=False)
