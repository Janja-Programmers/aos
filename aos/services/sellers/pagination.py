"""Stable keyset cursors for public Seller discovery."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from .errors import SellerValidationError

_CURSOR_VERSION = 1


def query_fingerprint(values: dict[str, Any]) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def encode_cursor(*, sort: str, query_key: str, values: list[Any]) -> str:
    payload = {"v": _CURSOR_VERSION, "s": sort, "q": query_key, "k": values}
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, *, sort: str, query_key: str, expected_keys: int) -> list[Any] | None:
    if not cursor:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise SellerValidationError("Invalid seller cursor.", code="INVALID_SELLER_PAGINATION")
    if not isinstance(payload, dict):
        raise SellerValidationError("Invalid seller cursor.", code="INVALID_SELLER_PAGINATION")
    if payload.get("v") != _CURSOR_VERSION or payload.get("s") != sort or payload.get("q") != query_key:
        raise SellerValidationError("Seller cursor does not match this query.", code="INVALID_SELLER_PAGINATION")
    keys = payload.get("k")
    if not isinstance(keys, list) or len(keys) != expected_keys:
        raise SellerValidationError("Invalid seller cursor.", code="INVALID_SELLER_PAGINATION")
    for value in keys:
        if isinstance(value, (dict, list)):
            raise SellerValidationError("Invalid seller cursor.", code="INVALID_SELLER_PAGINATION")
        if isinstance(value, str) and len(value) > 256:
            raise SellerValidationError("Invalid seller cursor.", code="INVALID_SELLER_PAGINATION")
    return keys
