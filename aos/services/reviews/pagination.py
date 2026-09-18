"""Opaque query-bound keyset cursors for Reviews."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any

from .errors import ReviewValidationError

_CURSOR_VERSION = 1


def query_fingerprint(values: dict[str, Any]) -> str:
    raw = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def encode_cursor(*, scope: str, sort: str, query_key: str, values: list[Any]) -> str:
    payload = {"v": _CURSOR_VERSION, "scope": scope, "sort": sort, "query": query_key, "keys": values}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(
    value: Any,
    *,
    scope: str,
    sort: str,
    query_key: str,
    expected_keys: int,
) -> list[Any] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 1000:
        raise ReviewValidationError("Invalid review cursor.", code="INVALID_REVIEW_CURSOR")
    try:
        token = value.strip()
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        raise ReviewValidationError("Invalid review cursor.", code="INVALID_REVIEW_CURSOR") from None
    if (
        not isinstance(payload, dict)
        or payload.get("v") != _CURSOR_VERSION
        or payload.get("scope") != scope
        or payload.get("sort") != sort
        or payload.get("query") != query_key
    ):
        raise ReviewValidationError("Review cursor does not match this query.", code="INVALID_REVIEW_CURSOR")
    keys = payload.get("keys")
    if not isinstance(keys, list) or len(keys) != expected_keys:
        raise ReviewValidationError("Invalid review cursor.", code="INVALID_REVIEW_CURSOR")
    for key in keys:
        if isinstance(key, (dict, list)):
            raise ReviewValidationError("Invalid review cursor.", code="INVALID_REVIEW_CURSOR")
        if isinstance(key, str) and len(key) > 256:
            raise ReviewValidationError("Invalid review cursor.", code="INVALID_REVIEW_CURSOR")
    _validate_cursor_keys(sort, keys)
    return keys


def _validate_cursor_keys(sort: str, keys: list[Any]) -> None:
    try:
        if sort in {"newest", "oldest"}:
            _datetime_key(keys[0]); _id_key(keys[1])
            return
        if sort == "helpful":
            count = int(keys[0])
            if count < 0:
                raise ValueError
            _datetime_key(keys[1]); _id_key(keys[2])
            return
        if sort in {"rating_high", "rating_low"}:
            rating = int(keys[0])
            if rating < 1 or rating > 5:
                raise ValueError
            _datetime_key(keys[1]); _id_key(keys[2])
            return
    except (TypeError, ValueError, OverflowError):
        raise ReviewValidationError("Invalid review cursor.", code="INVALID_REVIEW_CURSOR") from None
    raise ReviewValidationError("Invalid review sort.", code="INVALID_REVIEW_SORT")


def _datetime_key(value: Any) -> None:
    parsed = datetime.fromisoformat(str(value or ""))
    if parsed.tzinfo is not None:
        raise ValueError


def _id_key(value: Any) -> None:
    text = str(value or "").strip()
    if not text or len(text) > 140:
        raise ValueError
