# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import hashlib

import frappe
from frappe.model.document import Document


ALLOWED_PURPOSES = {"email_verification", "password_reset", "account_restore"}


def verification_name(user_name: str, purpose: str) -> str:
    """Return the fixed-length primary key for one user/purpose security row."""
    raw = f"{str(user_name).strip()}\0{str(purpose).strip()}".encode("utf-8")
    return f"authv-{hashlib.sha256(raw).hexdigest()}"


class AOSEmailVerification(Document):
    def autoname(self):
        if not self.user or not self.purpose:
            frappe.throw("User and Purpose are required to generate the name")

        self.user = str(self.user).strip()
        self.purpose = str(self.purpose).strip()
        self.name = verification_name(self.user, self.purpose)

    def validate(self):
        self._validate_user()
        self._validate_purpose()
        self._validate_email()

    def _validate_user(self):
        self.user = (self.user or "").strip()
        if not self.user:
            frappe.throw("User is required.", frappe.ValidationError)
        if not frappe.db.exists("User", self.user):
            frappe.throw("Invalid user.", frappe.ValidationError)

    def _validate_purpose(self):
        self.purpose = (self.purpose or "").strip()
        if self.purpose not in ALLOWED_PURPOSES:
            frappe.throw("Invalid verification purpose.", frappe.ValidationError)

    def _validate_email(self):
        self.email = (self.email or "").strip().lower()
        if not self.email:
            frappe.throw("Email is required.", frappe.ValidationError)
