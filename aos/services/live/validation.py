"""Strict public request validation for AOS Live v1 endpoints."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Pattern

from .constants import MAX_CURSOR_LENGTH, MAX_REASON_LENGTH, MAX_SESSION_ID_LENGTH, MAX_TITLE_LENGTH
from .errors import LiveError

LIVE_ID_RE = re.compile(r"^LIVE-\d{4}-\d{5}$")
SAFE_ROW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$")
ACCOUNT_REFERENCE_RE = re.compile(r"^(?:ACC-[A-Z2-7]{20}|[^\s]{1,140})$")


@dataclass(frozen=True)
class EndpointSpec:
    allowed_fields: frozenset[str]
    aliases: tuple[tuple[str, ...], ...] = ()
    id_fields: tuple[tuple[str, Pattern[str]], ...] = ()


def normalize_text(value: Any, *, field: str, max_length: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise LiveError(f"{field} must be text.", data={"field": field})
    text = unicodedata.normalize("NFC", value).strip()
    if required and not text:
        raise LiveError(f"{field} is required.", data={"field": field})
    if len(text) > max_length:
        raise LiveError(f"{field} is too long.", code="LIVE_INPUT_TOO_LARGE", http_status=413, data={"field": field})
    if "\x00" in text:
        raise LiveError(f"Invalid {field}.", data={"field": field})
    return text


def validate_public_kwargs(kwargs: Mapping[str, Any], spec: EndpointSpec) -> dict[str, Any]:
    clean = dict(kwargs or {})
    unknown = sorted(set(clean) - set(spec.allowed_fields))
    if unknown:
        raise LiveError(
            "Unsupported Live request field.",
            code="LIVE_UNKNOWN_FIELD",
            data={"fields": unknown[:10]},
        )
    for group in spec.aliases:
        supplied = [field for field in group if clean.get(field) not in (None, "")]
        if len(supplied) > 1:
            values = {str(clean[field]).strip() for field in supplied}
            if len(values) > 1:
                raise LiveError(
                    "Conflicting Live request aliases.",
                    code="LIVE_ALIAS_CONFLICT",
                    data={"fields": supplied},
                )
    for field, pattern in spec.id_fields:
        value = clean.get(field)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
            raise LiveError(
                "Invalid public identifier.",
                code="LIVE_INVALID_IDENTIFIER",
                data={"field": field},
            )
        clean[field] = value.strip()
    scalar_text_fields = {
        "cover_image": (2048, False),
        "live_cover_media": (140, False),
        "cover_image_media": (140, False),
        "media_id": (140, False),
        "content": (500, True),
        "idempotency_key": (128, False),
        "reaction_type": (24, True),
        "action": (24, True),
        "status": (32, False),
    }
    for field, (max_length, required) in scalar_text_fields.items():
        if field in clean and clean.get(field) is not None:
            clean[field] = normalize_text(
                clean[field], field=field, max_length=max_length, required=required
            )
    if "session_id" in clean and clean.get("session_id") not in (None, ""):
        clean["session_id"] = normalize_text(
            clean["session_id"], field="session_id", max_length=MAX_SESSION_ID_LENGTH, required=True
        )
    if "title" in clean and clean.get("title") is not None:
        clean["title"] = normalize_text(clean["title"], field="title", max_length=MAX_TITLE_LENGTH, required=True)
    if "reason" in clean and clean.get("reason") is not None:
        clean["reason"] = normalize_text(clean["reason"], field="reason", max_length=MAX_REASON_LENGTH)
    if "cursor" in clean and clean.get("cursor") not in (None, ""):
        clean["cursor"] = normalize_text(clean["cursor"], field="cursor", max_length=MAX_CURSOR_LENGTH, required=True)
    if clean.get("cursor") and clean.get("start") not in (None, "", 0, "0"):
        raise LiveError(
            "cursor and start cannot be combined.",
            code="LIVE_PAGINATION_CONFLICT",
            http_status=422,
        )
    for field, minimum, maximum in (("limit", 1, 100), ("start", 0, 10000)):
        if field not in clean or clean.get(field) in (None, ""):
            continue
        value = clean[field]
        if isinstance(value, bool):
            raise LiveError("Invalid pagination value.", data={"field": field})
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise LiveError("Invalid pagination value.", data={"field": field})
        if parsed < minimum or parsed > maximum:
            raise LiveError("Invalid pagination value.", data={"field": field})
        clean[field] = parsed
    return clean
