# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.accounts.constants import (
    ACCOUNT_STATUSES,
    ACCOUNT_STATUS_DELETED,
    PURGE_STATUSES,
    PURGE_STATUS_PENDING,
)
from aos.services.accounts.identity import generate_public_account_id, normalize_public_account_id
from aos.services.accounts.validation import (
    validate_bio,
    validate_date_of_birth,
    validate_display_name,
    validate_gender,
    validate_legal_name,
    validate_phone,
)


class AOSProfile(Document):
    """Canonical AOS account profile.

    ``name`` is the immutable opaque ACC-* account id. ``user`` links the
    profile to Frappe's authentication identity. Product profile fields live
    here; Frappe User is not a second source of truth for them.
    """

    def autoname(self):
        self.name = generate_public_account_id()

    def validate(self):
        self._validate_name()
        self._validate_user()
        self._validate_account_state()
        self._validate_purge_state()
        self._validate_profile_fields()

    def _validate_name(self):
        normalized = normalize_public_account_id(self.name)
        if not normalized:
            frappe.throw("Invalid account id.", frappe.ValidationError)
        if not self.is_new() and self.name != normalized:
            frappe.throw("Account id is immutable.", frappe.ValidationError)
        self.name = normalized

    def _validate_user(self):
        self.user = str(self.user or "").strip()
        if not self.user or not frappe.db.exists("User", self.user):
            frappe.throw("Invalid user.", frappe.ValidationError)

    def _validate_account_state(self):
        status = str(self.account_status or "Active").strip()
        if status not in ACCOUNT_STATUSES:
            frappe.throw("Invalid account status.", frappe.ValidationError)
        self.account_status = status

    def _validate_purge_state(self):
        status = str(self.purge_status or "").strip()
        if status and status not in PURGE_STATUSES:
            frappe.throw("Invalid permanent deletion status.", frappe.ValidationError)
        if self.account_status != ACCOUNT_STATUS_DELETED and status:
            frappe.throw("Only deleted accounts may be permanently purged.", frappe.ValidationError)
        if self.account_status == ACCOUNT_STATUS_DELETED and not status:
            status = PURGE_STATUS_PENDING
        self.purge_status = status

    def _validate_profile_fields(self):
        try:
            self.display_name = validate_display_name(self.display_name or self._fallback_display_name())
            self.legal_name = validate_legal_name(self.legal_name)
            self.bio = validate_bio(self.bio)
            self.phone = validate_phone(self.phone)
            self.date_of_birth = validate_date_of_birth(self.date_of_birth)
            self.gender = validate_gender(self.gender)
        except ValueError as exc:
            frappe.throw(str(exc), frappe.ValidationError)

    def _fallback_display_name(self) -> str:
        return str(frappe.db.get_value("User", self.user, "full_name") or "AOS User").strip()
