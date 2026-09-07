"""Privacy-safe stable AOS account identity."""

from __future__ import annotations

import base64
import re
import secrets
from typing import Any

import frappe

from .constants import PUBLIC_ACCOUNT_ID_PREFIX, PUBLIC_ACCOUNT_ID_RANDOM_BYTES

_PUBLIC_ID_RE = re.compile(r"^ACC-[A-Z2-7]{20}$")


def normalize_public_account_id(value: Any) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if _PUBLIC_ID_RE.fullmatch(candidate) else ""


def generate_public_account_id() -> str:
    """Generate an opaque immutable identifier without embedding account data."""
    token = base64.b32encode(secrets.token_bytes(PUBLIC_ACCOUNT_ID_RANDOM_BYTES)).decode("ascii").rstrip("=")
    return f"{PUBLIC_ACCOUNT_ID_PREFIX}{token}"


def ensure_public_account_id(profile_or_user: Any) -> str:
    """Return the immutable AOS Profile primary key for an existing profile."""
    if hasattr(profile_or_user, "doctype"):
        name = str(getattr(profile_or_user, "name", "") or "")
    else:
        user = str(profile_or_user or "").strip()
        name = str(frappe.db.get_value("AOS Profile", {"user": user}, "name") or "")
    normalized = normalize_public_account_id(name)
    if not normalized:
        raise RuntimeError("AOS account identity invariant is missing")
    return normalized


def profile_name_for_user(user: str | None) -> str | None:
    user = str(user or "").strip()
    if not user:
        return None
    value = frappe.db.get_value("AOS Profile", {"user": user}, "name")
    return normalize_public_account_id(value) or None


def get_profile_for_user(user: str):
    name = profile_name_for_user(user)
    return frappe.get_doc("AOS Profile", name) if name else None


def public_account_id_for_user(user: str | None) -> str | None:
    user = str(user or "").strip()
    if not user:
        return None
    return profile_name_for_user(user)


def resolve_account_reference(reference: Any) -> str | None:
    """Resolve a public ACC-* account id to the internal Frappe User.name.

    Public APIs deliberately do not accept emails/User.name as account ids.
    """
    account_id = normalize_public_account_id(reference)
    if not account_id:
        return None
    return frappe.db.get_value("AOS Profile", account_id, "user")
