# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.accounts.constants import ACCOUNT_STATUSES, ACCOUNT_STATUS_DELETED
from aos.services.accounts.identity import ensure_public_account_id, normalize_public_account_id
from aos.services.accounts.validation import (
    validate_bio,
    validate_date_of_birth,
    validate_display_name,
    validate_gender,
    validate_legal_name,
    validate_location,
    validate_phone,
)


class AOSProfile(Document):
    """Canonical persisted account profile with immutable public identity."""

    def before_insert(self):
        if not self.public_id:
            # The identity helper persists existing documents; new documents only
            # need a collision-resistant value assigned before insert.
            from aos.services.accounts.identity import generate_public_account_id

            for _ in range(8):
                candidate = generate_public_account_id()
                if not frappe.db.exists("AOS Profile", {"public_id": candidate}):
                    self.public_id = candidate
                    break
            if not self.public_id:
                frappe.throw("Unable to allocate public account id.", frappe.ValidationError)

    def validate(self):
        self._validate_user()
        self._validate_public_id()
        self._validate_account_state()
        self._validate_profile_fields()

    def after_insert(self):
        ensure_public_account_id(self)

    def _validate_user(self):
        self.user = str(self.user or "").strip()
        if not self.user or not frappe.db.exists("User", self.user):
            frappe.throw("Invalid user.", frappe.ValidationError)

    def _validate_public_id(self):
        normalized = normalize_public_account_id(self.public_id)
        if not normalized:
            frappe.throw("Invalid public account id.", frappe.ValidationError)
        self.public_id = normalized
        if not self.is_new():
            previous = frappe.db.get_value("AOS Profile", self.name, "public_id")
            if previous and previous != normalized:
                frappe.throw("Public account id is immutable.", frappe.ValidationError)

    def _validate_account_state(self):
        status = str(self.account_status or "Active").strip()
        if status not in ACCOUNT_STATUSES:
            frappe.throw("Invalid account status.", frappe.ValidationError)
        self.account_status = status
        if status == ACCOUNT_STATUS_DELETED:
            self.is_deleted = 1
        elif int(self.is_deleted or 0):
            frappe.throw("Only deleted accounts may set is_deleted.", frappe.ValidationError)

    def _validate_profile_fields(self):
        try:
            self.display_name = validate_display_name(self.display_name or self._fallback_display_name())
            self.legal_name = validate_legal_name(self.legal_name)
            self.bio = validate_bio(self.bio)
            self.phone = validate_phone(self.phone)
            self.date_of_birth = validate_date_of_birth(self.date_of_birth)
            self.gender = validate_gender(self.gender)
            self.location = validate_location(self.location)
        except ValueError as exc:
            frappe.throw(str(exc), frappe.ValidationError)

    def _fallback_display_name(self) -> str:
        return str(frappe.db.get_value("User", self.user, "full_name") or self.user).strip()
