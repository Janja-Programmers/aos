"""Strict public request validation for AOS Calls v1 endpoints."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Mapping, Pattern

from .errors import CallError
from .identifiers import PUBLIC_CALL_ID_RE

CALL_ID_RE = PUBLIC_CALL_ID_RE
CONVERSATION_ID_RE = re.compile(r"^CONV-\d{4}-\d{5}$")
MAX_REQUEST_BYTES = 32 * 1024
MAX_TEXT_LENGTH = 128
MAX_CALL_IDS = 100
TRANSPORT_FIELDS = frozenset({"cmd"})


@dataclass(frozen=True)
class EndpointSpec:
    allowed_fields: frozenset[str]
    id_fields: tuple[tuple[str, Pattern[str]], ...] = ()


def normalize_text(value: Any, *, field: str, max_length: int = MAX_TEXT_LENGTH) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise CallError(f"{field} must be text.", data={"field": field})
    text = unicodedata.normalize("NFC", value).strip()
    if "\x00" in text:
        raise CallError(f"Invalid {field}.", data={"field": field})
    if len(text) > max_length:
        raise CallError(
            f"{field} is too long.",
            code="CALL_INPUT_TOO_LARGE",
            http_status=413,
            data={"field": field},
        )
    return text


def _validate_shape(clean: Mapping[str, Any]) -> None:
    try:
        encoded = json.dumps(clean, separators=(",", ":"), default=str).encode("utf-8")
    except Exception as exc:
        raise CallError("Invalid call request payload.") from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise CallError("Call request is too large.", code="CALL_INPUT_TOO_LARGE", http_status=413)


def _normalize_call_ids(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    raw = value
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except Exception:
            raw = [text]
    if not isinstance(raw, (list, tuple, set)):
        raw = [raw]
    if len(raw) > MAX_CALL_IDS:
        raise CallError("Too many call identifiers.", code="CALL_INPUT_TOO_LARGE", http_status=413)
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not CALL_ID_RE.fullmatch(item.strip()):
            raise CallError("Invalid public call identifier.", code="CALL_INVALID_IDENTIFIER", data={"field": "call_ids"})
        result.append(item.strip())
    return list(dict.fromkeys(result))


def validate_public_kwargs(kwargs: Mapping[str, Any], spec: EndpointSpec) -> dict[str, Any]:
    raw = dict(kwargs or {})
    _validate_shape(raw)
    clean = {key: value for key, value in raw.items() if key not in TRANSPORT_FIELDS}
    unknown = sorted(set(clean) - set(spec.allowed_fields))
    if unknown:
        raise CallError(
            "Unsupported Calls request field.",
            code="CALL_UNKNOWN_FIELD",
            data={"fields": unknown[:10]},
        )


    for field, pattern in spec.id_fields:
        value = clean.get(field)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
            raise CallError("Invalid public identifier.", code="CALL_INVALID_IDENTIFIER", data={"field": field})
        clean[field] = value.strip()

    for field in ("call_type", "action", "type"):
        if field in clean and clean.get(field) is not None:
            clean[field] = normalize_text(clean[field], field=field)

    if "cursor_created_at" in clean and clean.get("cursor_created_at") not in (None, ""):
        cursor = normalize_text(clean["cursor_created_at"], field="cursor_created_at", max_length=64)
        try:
            datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CallError(
                "Invalid call-history cursor.",
                code="CALL_INVALID_CURSOR",
                data={"field": "cursor_created_at"},
            ) from exc
        clean["cursor_created_at"] = cursor

    if "limit" in clean and clean.get("limit") not in (None, ""):
        value = clean["limit"]
        if isinstance(value, bool):
            raise CallError("Invalid pagination limit.", data={"field": "limit"})
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise CallError("Invalid pagination limit.", data={"field": "limit"}) from exc
        if parsed < 1 or parsed > 100:
            raise CallError("Invalid pagination limit.", data={"field": "limit"})
        clean["limit"] = parsed

    if "call_ids" in clean:
        clean["call_ids"] = _normalize_call_ids(clean.get("call_ids"))

    return clean
