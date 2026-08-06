"""Signed opaque cursor helpers for Live feeds."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import frappe

from .errors import LiveError


def _secret() -> bytes:
    value = ""
    try:
        value = str(getattr(frappe.local, "conf", {}).get("encryption_key") or "")
    except Exception:
        pass
    if not value:
        try:
            value = str((frappe.get_site_config() or {}).get("encryption_key") or "")
        except Exception:
            pass
    if not value:
        raise LiveError(
            "Live cursor signing is unavailable.",
            code="LIVE_DEPENDENCY_UNAVAILABLE",
            http_status=503,
        )
    return value.encode("utf-8")


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> dict[str, Any]:
    try:
        encoded = str(value or "").strip()
        if not encoded or len(encoded) > 1024:
            raise ValueError
        padded = encoded + "=" * (-len(encoded) % 4)
        packed = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(packed) <= 32:
            raise ValueError
        raw, signature = packed[:-32], packed[-32:]
        expected = hmac.new(_secret(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except LiveError:
        raise
    except Exception:
        raise LiveError("Invalid Live cursor.", code="LIVE_INVALID_CURSOR", http_status=422)
