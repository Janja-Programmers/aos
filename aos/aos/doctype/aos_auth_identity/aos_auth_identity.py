"""Durable OIDC provider-subject binding without persisting the raw provider subject."""

from __future__ import annotations

import re

import frappe
from frappe.model.document import Document

from aos.utils.privacy import opaque_digest

ALLOWED_PROVIDERS = {"google", "apple"}
_IDENTITY_NAME_RE = re.compile(r"^(google|apple)-[0-9a-f]{64}$")


def identity_name(provider: str, subject: str) -> str:
    provider = str(provider or "").strip().lower()
    subject = str(subject or "").strip()
    if provider not in ALLOWED_PROVIDERS or not subject or len(subject) > 255:
        raise ValueError("Invalid provider identity")
    digest = opaque_digest(f"oidc-subject\0{provider}\0{subject}")
    return f"{provider}-{digest}"


def user_provider_key(user: str, provider: str) -> str:
    return opaque_digest(f"oidc-user-provider\0{str(user).strip()}\0{str(provider).strip().lower()}")


class AOSAuthIdentity(Document):
    def autoname(self):
        # Raw provider subject is request-local and never becomes a persisted field.
        subject = str(getattr(self.flags, "aos_oidc_subject", "") or "").strip()
        self.provider = str(self.provider or "").strip().lower()
        try:
            self.name = identity_name(self.provider, subject)
        except ValueError:
            frappe.throw("Invalid provider identity.", frappe.ValidationError)

    def validate(self):
        self.provider = str(self.provider or "").strip().lower()
        self.user = str(self.user or "").strip()
        if self.provider not in ALLOWED_PROVIDERS:
            frappe.throw("Invalid authentication provider.", frappe.ValidationError)
        if not _IDENTITY_NAME_RE.fullmatch(str(self.name or "")) or not str(self.name).startswith(f"{self.provider}-"):
            frappe.throw("Invalid provider identity.", frappe.ValidationError)
        if not self.user or not frappe.db.exists("User", self.user):
            frappe.throw("Invalid user.", frappe.ValidationError)
        self.user_provider_key = user_provider_key(self.user, self.provider)
