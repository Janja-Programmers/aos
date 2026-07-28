"""Opaque public Seller identity and legacy reference resolution."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import Any

import frappe

_PUBLIC_ID_RE = re.compile(r"^SELLER-[A-Z2-7]{20}$")
_RANDOM_BYTES = 12


def normalize_public_seller_id(value: Any) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if _PUBLIC_ID_RE.fullmatch(candidate) else ""


def generate_public_seller_id() -> str:
    token = base64.b32encode(secrets.token_bytes(_RANDOM_BYTES)).decode("ascii").rstrip("=")
    return f"SELLER-{token}"


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
        try:
            value = f"aos:{frappe.local.site}"
        except Exception:
            value = "aos:unconfigured-site"
    return value.encode("utf-8")


def migration_fallback_public_seller_id(seller_name: str) -> str:
    digest = hmac.new(
        _site_secret(),
        str(seller_name or "").encode("utf-8"),
        hashlib.sha256,
    ).digest()
    token = base64.b32encode(digest[:_RANDOM_BYTES]).decode("ascii").rstrip("=")
    return f"SELLER-{token}"


def ensure_public_seller_id(seller_or_name: Any) -> str:
    if hasattr(seller_or_name, "doctype"):
        seller = seller_or_name
    else:
        name = str(seller_or_name or "").strip()
        if not name or not frappe.db.exists("AOS Seller", name):
            return migration_fallback_public_seller_id(name)
        seller = frappe.get_doc("AOS Seller", name)
    existing = normalize_public_seller_id(getattr(seller, "public_id", ""))
    if existing:
        return existing
    for _ in range(8):
        candidate = generate_public_seller_id()
        if not frappe.db.exists("AOS Seller", {"public_id": candidate}):
            if getattr(seller, "is_new", lambda: False)():
                seller.public_id = candidate
            else:
                seller.db_set("public_id", candidate, update_modified=False)
                seller.public_id = candidate
            return candidate
    raise RuntimeError("Unable to allocate public seller id")


def public_seller_id_for_name(seller_name: Any) -> str | None:
    name = str(seller_name or "").strip()
    if not name:
        return None
    if not frappe.db.exists("AOS Seller", name):
        return migration_fallback_public_seller_id(name)
    value = frappe.db.get_value("AOS Seller", name, "public_id")
    return normalize_public_seller_id(value) or migration_fallback_public_seller_id(name)


def resolve_seller_reference(reference: Any, *, allow_legacy: bool = True) -> str | None:
    raw = str(reference or "").strip()
    if not raw:
        return None
    public_id = normalize_public_seller_id(raw)
    if public_id:
        return frappe.db.get_value("AOS Seller", {"public_id": public_id}, "name")
    if not allow_legacy:
        return None
    if frappe.db.exists("AOS Seller", raw):
        return raw
    # Seller records are historically named from User.name. This legacy
    # fallback is accepted on input only and is never returned publicly.
    return frappe.db.get_value("AOS Seller", {"user": raw}, "name")
