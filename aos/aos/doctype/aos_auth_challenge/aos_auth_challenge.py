# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import hashlib

import frappe
from frappe.model.document import Document

ALLOWED_PURPOSES = {"email_verification", "password_reset", "account_restore", "two_factor"}


def challenge_name(user_name: str, purpose: str) -> str:
    """Return the fixed-length primary key for one user/purpose auth challenge."""
    raw = f"{str(user_name).strip()}\0{str(purpose).strip()}".encode("utf-8")
    return f"authc-{hashlib.sha256(raw).hexdigest()}"


class AOSAuthChallenge(Document):
    def autoname(self):
        if not self.user or not self.purpose:
            frappe.throw("User and Purpose are required to generate the name")
        self.user = str(self.user).strip()
        self.purpose = str(self.purpose).strip()
        self.name = challenge_name(self.user, self.purpose)

    def validate(self):
        self.user = str(self.user or "").strip()
        self.purpose = str(self.purpose or "").strip()
        if not self.user or not frappe.db.exists("User", self.user):
            frappe.throw("Invalid user.", frappe.ValidationError)
        if self.purpose not in ALLOWED_PURPOSES:
            frappe.throw("Invalid authentication challenge purpose.", frappe.ValidationError)
