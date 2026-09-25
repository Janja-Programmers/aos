"""Opaque keyset cursor helpers for the private Activity timeline."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from frappe.utils import get_datetime, get_datetime_str

from .constants import MAX_CURSOR_LENGTH
from .errors import ActivityValidationError
from .identity import normalize_activity_id

_CURSOR_VERSION = 1


def _scope(*, user: str, group: str, activity_type: str) -> str:
    material = f"{str(user or '').strip()}\x1f{group}\x1f{activity_type}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def encode_cursor(*, user: str, group: str, activity_type: str, row: Any) -> str:
    value = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
    payload = {
        "v": _CURSOR_VERSION,
        "s": _scope(user=user, group=group, activity_type=activity_type),
        "l": get_datetime_str(value("last_occurrence_at")),
        "c": get_datetime_str(value("creation")),
        "i": str(value("public_id") or ""),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: Any, *, user: str, group: str, activity_type: str) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ActivityValidationError("Invalid activity cursor.")
    token = value.strip()
    if not token or len(token) > MAX_CURSOR_LENGTH:
        raise ActivityValidationError("Invalid activity cursor.")
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True).decode("utf-8"))
    except Exception as exc:
        raise ActivityValidationError("Invalid activity cursor.") from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise ActivityValidationError("Invalid activity cursor.")
    if payload.get("s") != _scope(user=user, group=group, activity_type=activity_type):
        raise ActivityValidationError("Invalid activity cursor.")
    public_id = normalize_activity_id(payload.get("i"))
    if not public_id:
        raise ActivityValidationError("Invalid activity cursor.")
    try:
        last = get_datetime(payload.get("l"))
        creation = get_datetime(payload.get("c"))
    except Exception as exc:
        raise ActivityValidationError("Invalid activity cursor.") from exc
    if not last or not creation:
        raise ActivityValidationError("Invalid activity cursor.")
    return {"last_occurrence_at": last, "creation": creation, "public_id": public_id}
