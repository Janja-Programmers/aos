"""Privacy-preserving stable identifiers for logs and observability."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import frappe


def _site_hmac_key() -> bytes | None:
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
    return value.encode("utf-8") if value else None


def opaque_digest(value: Any) -> str:
    raw = str(value or "").encode("utf-8")
    key = _site_hmac_key()
    if not raw or not key:
        raise RuntimeError("Site encryption key is required for opaque identity digests")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()


def opaque_identifier(value: Any, *, length: int = 16) -> str:
    """Return a site-keyed non-reversible correlation id.

    If the site encryption key is unavailable during an install/bootstrap edge
    path, do not fall back to an externally reproducible plain hash.
    """
    raw = str(value or "").encode("utf-8")
    key = _site_hmac_key()
    if not raw or not key:
        return "unavailable"
    return hmac.new(key, raw, hashlib.sha256).hexdigest()[: max(8, min(int(length), 64))]
