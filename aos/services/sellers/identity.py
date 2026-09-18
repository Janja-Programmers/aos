"""Opaque public Seller identity.

Public Seller references are never internal DocType names, User IDs, e-mail
addresses, or sequential identifiers. Fresh installs allocate one immutable
random public ID at Seller creation and all public lookups require that form.
"""

from __future__ import annotations

import base64
import re
import secrets
from typing import Any

import frappe

from .errors import SellerNotFoundError

_PUBLIC_ID_RE = re.compile(r"^SELLER-[A-Z2-7]{20}$")
_RANDOM_BYTES = 12


def normalize_public_seller_id(value: Any) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if _PUBLIC_ID_RE.fullmatch(candidate) else ""


def require_public_seller_id(value: Any) -> str:
    public_id = normalize_public_seller_id(value)
    if not public_id:
        raise SellerNotFoundError("Seller not found.")
    return public_id


def generate_public_seller_id() -> str:
    token = base64.b32encode(secrets.token_bytes(_RANDOM_BYTES)).decode("ascii").rstrip("=")
    return f"SELLER-{token}"


def ensure_public_seller_id(seller) -> str:
    """Ensure a Seller document has an immutable random public identifier.

    The database uniqueness constraint is the final collision guard; Seller
    creation retries a duplicate-key race with a fresh random identifier.
    """

    existing = normalize_public_seller_id(getattr(seller, "public_id", ""))
    if existing:
        return existing
    candidate = generate_public_seller_id()
    seller.public_id = candidate
    return candidate


def resolve_public_seller_id(value: Any) -> str | None:
    public_id = normalize_public_seller_id(value)
    if not public_id:
        return None
    return frappe.db.get_value("AOS Seller", {"public_id": public_id}, "name")


def public_seller_id_for_name(seller_name: Any) -> str | None:
    name = str(seller_name or "").strip()
    if not name:
        return None
    return normalize_public_seller_id(frappe.db.get_value("AOS Seller", name, "public_id")) or None
