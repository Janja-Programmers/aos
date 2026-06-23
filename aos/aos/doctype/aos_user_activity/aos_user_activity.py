# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


VALID_STATUSES = {"Active", "Hidden", "Cleared"}


class AOSUserActivity(Document):
    def validate(self):
        self._validate_user()
        self._validate_status()
        self._set_defaults()
        self._normalize_count()

    def _validate_user(self):
        if not self.user:
            frappe.throw("User is required")

        if not frappe.db.exists("User", self.user):
            frappe.throw("Invalid user")

    def _validate_status(self):
        if not self.status:
            self.status = "Active"

        if self.status not in VALID_STATUSES:
            frappe.throw("Invalid activity status")

    def _set_defaults(self):
        now = now_datetime()

        if not self.occurred_at:
            self.occurred_at = now

        if not self.last_occurrence_at:
            self.last_occurrence_at = self.occurred_at or now

        if not self.activity_group:
            self.activity_group = "Other"

    def _normalize_count(self):
        try:
            self.count = max(int(self.count or 0), 1)
        except Exception:
            self.count = 1
