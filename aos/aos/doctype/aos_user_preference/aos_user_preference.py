# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class AOSUserPreference(Document):
    """Per-user AOS market/profile preference.

    Naming is by ``user`` and the DocType also has a unique ``user`` field, so
    there must be at most one preference row per Frappe User.
    """

    def validate(self):
        self._validate_user()
        self._validate_links()

    def _validate_user(self):
        self.user = (self.user or "").strip()
        if not self.user:
            frappe.throw("User is required.", frappe.ValidationError)

        if not frappe.db.exists("User", self.user):
            frappe.throw("Invalid user.", frappe.ValidationError)

    def _validate_links(self):
        if not frappe.db.exists("Country", self.country):
            frappe.throw("Invalid country.", frappe.ValidationError)

        if not frappe.db.exists("Language", self.language):
            frappe.throw("Invalid language.", frappe.ValidationError)

        if not frappe.db.exists("Currency", self.currency):
            frappe.throw("Invalid currency.", frappe.ValidationError)
