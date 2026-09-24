"""Opaque deterministic cursor helpers for Chat list endpoints.

Cursors are integrity-neutral pagination positions, not authorization tokens.
Every query still enforces membership/visibility independently.  The encoded
shape is versioned so malformed or stale client values fail closed.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Mapping

from .errors import ChatError

_CURSOR_VERSION = 1
_MAX_CURSOR_BYTES = 512


def encode_cursor(kind: str, values: Mapping[str, Any]) -> str:
    payload = {"v": _CURSOR_VERSION, "k": str(kind), **dict(values)}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: Any, *, kind: str, required_keys: tuple[str, ...]) -> dict[str, Any] | None:
    if cursor in (None, ""):
        return None
    if not isinstance(cursor, str) or len(cursor) > _MAX_CURSOR_BYTES:
        raise ChatError("Invalid Chat cursor.", code="CHAT_INVALID_CURSOR", data={"field": "cursor"})
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise ChatError("Invalid Chat cursor.", code="CHAT_INVALID_CURSOR", data={"field": "cursor"}) from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION or payload.get("k") != kind:
        raise ChatError("Invalid Chat cursor.", code="CHAT_INVALID_CURSOR", data={"field": "cursor"})
    if set(payload) != {"v", "k", *required_keys}:
        raise ChatError("Invalid Chat cursor.", code="CHAT_INVALID_CURSOR", data={"field": "cursor"})
    for key in required_keys:
        value = payload.get(key)
        if value in (None, "") or isinstance(value, (dict, list, tuple, set)):
            raise ChatError("Invalid Chat cursor.", code="CHAT_INVALID_CURSOR", data={"field": "cursor"})
    return payload
