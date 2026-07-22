"""Privacy-safe public account identity and legacy-reference resolution."""

from __future__ import annotations

import hashlib
import hmac
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
    import base64

    token = base64.b32encode(secrets.token_bytes(PUBLIC_ACCOUNT_ID_RANDOM_BYTES)).decode("ascii").rstrip("=")
    return f"{PUBLIC_ACCOUNT_ID_PREFIX}{token}"


def _site_secret() -> bytes:
    value = ""
    try:
        value = str(getattr(frappe.local, "conf", {}).get("encryption_key") or "")
    except Exception:
        value = ""
    if not value:
        try:
            value = str((frappe.get_site_config() or {}).get("encryption_key") or "")
        except Exception:
            value = ""
    if not value:
        # Frappe production sites have encryption_key. This last-resort value avoids
        # exposing an email during partially configured install/migrate test paths.
        try:
            value = f"aos:{frappe.local.site}"
        except Exception:
            value = "aos:unconfigured-site"
    return value.encode("utf-8")


def migration_fallback_public_id(user: str) -> str:
    """Return a deterministic opaque fallback for partially migrated profiles."""
    digest = hmac.new(_site_secret(), str(user or "").encode("utf-8"), hashlib.sha256).digest()
    import base64

    token = base64.b32encode(digest[:PUBLIC_ACCOUNT_ID_RANDOM_BYTES]).decode("ascii").rstrip("=")
    return f"{PUBLIC_ACCOUNT_ID_PREFIX}{token}"


def ensure_public_account_id(profile_or_user: Any) -> str:
    """Ensure and persist an immutable public ID on an AOS Profile."""
    if hasattr(profile_or_user, "doctype"):
        profile = profile_or_user
    else:
        user = str(profile_or_user or "").strip()
        if not user or not frappe.db.exists("AOS Profile", user):
            return migration_fallback_public_id(user)
        profile = frappe.get_doc("AOS Profile", user)

    existing = normalize_public_account_id(getattr(profile, "public_id", ""))
    if existing:
        return existing

    for _ in range(8):
        candidate = generate_public_account_id()
        if not frappe.db.exists("AOS Profile", {"public_id": candidate}):
            profile.db_set("public_id", candidate, update_modified=False)
            profile.public_id = candidate
            return candidate

    raise RuntimeError("Unable to allocate public account id")


def public_account_id_for_user(user: str | None) -> str | None:
    user = str(user or "").strip()
    if not user:
        return None
    if not frappe.db.exists("AOS Profile", user):
        return migration_fallback_public_id(user)
    value = frappe.db.get_value("AOS Profile", user, "public_id")
    return normalize_public_account_id(value) or migration_fallback_public_id(user)


def resolve_account_reference(reference: Any, *, allow_legacy: bool = True) -> str | None:
    """Resolve opaque ACC-* or legacy User.name inputs to the internal User.name."""
    raw = str(reference or "").strip()
    if not raw:
        return None
    public_id = normalize_public_account_id(raw)
    if public_id:
        return frappe.db.get_value("AOS Profile", {"public_id": public_id}, "user")
    if not allow_legacy:
        return None
    if frappe.db.exists("User", raw):
        return raw
    lowered = raw.lower()
    return frappe.db.get_value("User", {"email": lowered}, "name")
