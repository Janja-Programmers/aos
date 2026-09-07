"""Repository helpers for durable social identity bindings."""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail
from aos.aos.doctype.aos_auth_identity.aos_auth_identity import identity_name


def get_bound_user(provider: str, subject: str) -> str | None:
    name = identity_name(provider, subject)
    return frappe.db.get_value("AOS Auth Identity", name, "user")


def bind_identity(*, provider: str, subject: str, user: str, email: str):
    name = identity_name(provider, subject)
    existing = frappe.db.get_value("AOS Auth Identity", name, ["user", "name"], as_dict=True)
    if existing:
        if existing.user != user:
            return fail("Social identity is already linked to another account.", error="SOCIAL_IDENTITY_CONFLICT")
        return None
    doc = frappe.new_doc("AOS Auth Identity")
    doc.provider = provider
    doc.subject = subject
    doc.user = user
    doc.email_at_link = email
    try:
        doc.insert(ignore_permissions=True)
        return None
    except frappe.DuplicateEntryError:
        # Either provider subject or one-provider-per-user uniqueness raced.
        bound = get_bound_user(provider, subject)
        if bound == user:
            return None
        return fail("Social identity cannot be linked automatically.", error="SOCIAL_IDENTITY_CONFLICT")
