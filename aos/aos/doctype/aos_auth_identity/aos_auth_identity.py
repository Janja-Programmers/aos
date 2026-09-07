"""Durable OIDC provider-subject binding for AOS accounts."""

from __future__ import annotations

import hashlib

import frappe
from frappe.model.document import Document

ALLOWED_PROVIDERS = {"google", "apple"}


def identity_name(provider: str, subject: str) -> str:
    provider = str(provider or "").strip().lower()
    subject = str(subject or "").strip()
    digest = hashlib.sha256(f"{provider}\0{subject}".encode("utf-8")).hexdigest()
    return f"{provider}-{digest}"


def user_provider_key(user: str, provider: str) -> str:
    return hashlib.sha256(f"{str(user).strip()}\0{str(provider).strip().lower()}".encode("utf-8")).hexdigest()


class AOSAuthIdentity(Document):
    def autoname(self):
        self.provider = str(self.provider or "").strip().lower()
        self.subject = str(self.subject or "").strip()
        self.name = identity_name(self.provider, self.subject)

    def validate(self):
        self.provider = str(self.provider or "").strip().lower()
        self.subject = str(self.subject or "").strip()
        self.user = str(self.user or "").strip()
        self.email_at_link = str(self.email_at_link or "").strip().lower()
        if self.provider not in ALLOWED_PROVIDERS:
            frappe.throw("Invalid authentication provider.", frappe.ValidationError)
        if not self.subject or len(self.subject) > 255:
            frappe.throw("Invalid provider subject.", frappe.ValidationError)
        if not self.user or not frappe.db.exists("User", self.user):
            frappe.throw("Invalid user.", frappe.ValidationError)
        self.user_provider_key = user_provider_key(self.user, self.provider)
