# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.activity.constants import (
    ACTIVE_STATUS,
    CLEARED_STATUS,
    HIDDEN_STATUS,
    VALID_ACTIVITY_GROUPS,
    VALID_ACTIVITY_STATUSES,
    VALID_ACTIVITY_TYPES,
)
from aos.services.activity_service import ActivityService


class AOSUserActivity(Document):
    """Server-owned private Activity Center projection."""

    def validate(self):
        self._validate_user()
        self._validate_group_and_type()
        self._validate_status()
        self._set_defaults()
        self._normalize_count()
        self._protect_integrity_key()
        self._protect_identity_fields()

    def _validate_user(self):
        if not self.user:
            frappe.throw("User is required")
        if not frappe.db.exists("User", self.user):
            frappe.throw("Invalid user")

    def _validate_group_and_type(self):
        if not self.activity_group:
            self.activity_group = "Other"
        if self.activity_group not in VALID_ACTIVITY_GROUPS:
            frappe.throw("Invalid activity group")
        if not self.activity_type or self.activity_type not in VALID_ACTIVITY_TYPES:
            frappe.throw("Invalid activity type")

    def _validate_status(self):
        if not self.status:
            self.status = ACTIVE_STATUS
        if self.status not in VALID_ACTIVITY_STATUSES:
            frappe.throw("Invalid activity status")
        if not self.is_new():
            previous = frappe.db.get_value(self.doctype, self.name, "status")
            if previous in {HIDDEN_STATUS, CLEARED_STATUS} and self.status != previous:
                frappe.throw("Completed activity history cannot be reactivated")

    def _set_defaults(self):
        now = now_datetime()
        if not self.occurred_at:
            self.occurred_at = now
        if not self.last_occurrence_at:
            self.last_occurrence_at = self.occurred_at or now

    def _normalize_count(self):
        try:
            self.count = max(int(self.count or 0), 1)
        except Exception:
            self.count = 1

    def _protect_integrity_key(self):
        unique_key = str(self.unique_key or "").strip()
        if self.status == ACTIVE_STATUS and unique_key:
            self.active_key = ActivityService.active_key_for(user=self.user, unique_key=unique_key)
        else:
            self.active_key = None

    def _protect_identity_fields(self):
        if self.is_new():
            return
        previous = frappe.db.get_value(
            self.doctype,
            self.name,
            ["user", "activity_type", "unique_key"],
            as_dict=True,
        )
        if not previous:
            return
        if str(previous.user or "") != str(self.user or ""):
            frappe.throw("Activity ownership cannot be changed")
        if str(previous.activity_type or "") != str(self.activity_type or ""):
            frappe.throw("Activity type cannot be changed")
        if str(previous.unique_key or "") != str(self.unique_key or ""):
            frappe.throw("Activity integrity key cannot be changed")
