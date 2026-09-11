# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.notifications.devices import (
    PushDeviceValidationError,
    get_token_hash,
    normalize_device_id,
    normalize_device_type,
    normalize_push_token,
    normalize_registration_kind,
)


class AOSPushToken(Document):
    def before_insert(self):
        self._validate_and_normalize(touch_last_seen=True)

    def validate(self):
        self._validate_and_normalize(touch_last_seen=False)

    def _validate_and_normalize(self, *, touch_last_seen: bool) -> None:
        user = str(self.user or "").strip()
        if not user or not frappe.db.exists("User", user):
            frappe.throw("User is required.")

        try:
            token = normalize_push_token(self.token)
            device_type = normalize_device_type(self.device_type)
            device_id = normalize_device_id(self.device_id)
            registration_kind = normalize_registration_kind(self.registration_kind)
        except PushDeviceValidationError as exc:
            frappe.throw(str(exc))

        self.user = user
        self.token = token
        self.token_hash = get_token_hash(token)
        self.device_type = device_type
        self.device_id = device_id
        self.registration_kind = registration_kind
        if self.is_active is None:
            self.is_active = 1
        self.is_active = 1 if bool(int(self.is_active or 0)) else 0
        if touch_last_seen and not self.last_used_at:
            self.last_used_at = now_datetime()
        self._sync_active_device_key()

    def _sync_active_device_key(self):
        device_id = str(self.device_id or "").strip()
        if bool(self.is_active) and self.user and device_id:
            self.active_device_key = f"{self.user}|{device_id}"
            return
        self.active_device_key = None

    def deactivate(self):
        if self.is_active:
            frappe.db.set_value(
                "AOS Push Token",
                self.name,
                {
                    "is_active": 0,
                    "active_device_key": None,
                    "last_used_at": now_datetime(),
                },
                update_modified=False,
            )

    def activate(self):
        if not self.is_active:
            self.is_active = 1
            self._sync_active_device_key()
            frappe.db.set_value(
                "AOS Push Token",
                self.name,
                {
                    "is_active": 1,
                    "active_device_key": self.active_device_key,
                    "last_used_at": now_datetime(),
                },
                update_modified=False,
            )
