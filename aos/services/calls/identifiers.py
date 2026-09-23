"""Opaque public identifiers for the Calls domain."""

from __future__ import annotations

import re
import secrets


PUBLIC_CALL_ID_PREFIX = "call_"
PUBLIC_CALL_ID_HEX_BYTES = 16
PUBLIC_CALL_ID_RE = re.compile(r"^call_[0-9a-f]{32}$")


def generate_public_call_id() -> str:
    """Generate a non-enumerable 128-bit client-facing call identifier."""
    return f"{PUBLIC_CALL_ID_PREFIX}{secrets.token_hex(PUBLIC_CALL_ID_HEX_BYTES)}"


def normalize_public_call_id(value: object) -> str:
    return str(value or "").strip()


def public_call_id(call) -> str:
    value = normalize_public_call_id(getattr(call, "public_id", None))
    if not PUBLIC_CALL_ID_RE.fullmatch(value):
        raise RuntimeError("Call public identifier is unavailable.")
    return value


def internal_call_name(public_id: object) -> str | None:
    """Resolve a validated client-visible call ID to the internal DocType name."""
    value = normalize_public_call_id(public_id)
    if not PUBLIC_CALL_ID_RE.fullmatch(value):
        return None
    import frappe

    result = frappe.db.get_value("AOS Call", {"public_id": value}, "name")
    return str(result) if result else None
