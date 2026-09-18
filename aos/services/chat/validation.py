"""Strict public request validation for AOS Chat v1 endpoints."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Pattern

from aos.services.media.identifiers import MEDIA_ID_RE

from .errors import ChatError

CONVERSATION_ID_RE = re.compile(r"^CONV-\d{4}-\d{5}$")
MESSAGE_ID_RE = re.compile(r"^MSG-\d{4}-\d{5}$")
LIVE_ID_RE = re.compile(r"^LIVE-\d{4}-\d{5}$")
SHORT_ID_RE = re.compile(r"^SHORT-\d{4}-\d{5}$")
AD_ID_RE = re.compile(r"^AD-\d{4}-\d{5}$")
PUBLIC_ACCOUNT_ID_RE = re.compile(r"^ACC-[A-Z2-7]{20}$")
SAFE_ROW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$")

MAX_REQUEST_BYTES = 64 * 1024
MAX_CONTENT_LENGTH = 4000
MAX_TRANSLATION_LANGUAGE_LENGTH = 32
MAX_EMOJI_LENGTH = 16
MAX_IDEMPOTENCY_KEY_LENGTH = 128
MAX_ATTACHMENTS = 10
MAX_MULTI_MESSAGE_IDS = 100
MAX_FORWARD_TARGETS = 20
MAX_JSON_DEPTH = 8

TRANSPORT_FIELDS = frozenset({"cmd"})


@dataclass(frozen=True)
class EndpointSpec:
    allowed_fields: frozenset[str]
    aliases: tuple[tuple[str, ...], ...] = ()
    id_fields: tuple[tuple[str, Pattern[str]], ...] = ()


def normalize_text(value: Any, *, field: str, max_length: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ChatError(f"{field} must be text.", data={"field": field})
    text = unicodedata.normalize("NFC", value).strip()
    if required and not text:
        raise ChatError(f"{field} is required.", data={"field": field})
    if "\x00" in text:
        raise ChatError(f"Invalid {field}.", data={"field": field})
    if len(text) > max_length:
        raise ChatError(
            f"{field} is too long.",
            code="CHAT_INPUT_TOO_LARGE",
            http_status=413,
            data={"field": field},
        )
    return text


def _depth(value: Any, current: int = 0) -> int:
    if current > MAX_JSON_DEPTH:
        return current
    if isinstance(value, dict):
        if not value:
            return current + 1
        return max(_depth(k, current + 1) for k in value) if not value.values() else max(
            [_depth(k, current + 1) for k in value] + [_depth(v, current + 1) for v in value.values()]
        )
    if isinstance(value, (list, tuple)):
        return current + 1 if not value else max(_depth(item, current + 1) for item in value)
    return current + 1


def _validate_shape(clean: Mapping[str, Any]) -> None:
    try:
        encoded = json.dumps(clean, separators=(",", ":"), default=str).encode("utf-8")
    except Exception as exc:
        raise ChatError("Invalid request payload.") from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ChatError("Chat request is too large.", code="CHAT_INPUT_TOO_LARGE", http_status=413)
    if _depth(clean) > MAX_JSON_DEPTH:
        raise ChatError("Chat request is too deeply nested.", code="CHAT_INPUT_TOO_LARGE", http_status=413)


def _normalize_id_list(value: Any, *, field: str, pattern: Pattern[str], maximum: int) -> list[str]:
    if value is None or value == "":
        return []
    raw = value if isinstance(value, list) else [value]
    if len(raw) > maximum:
        raise ChatError("Too many identifiers.", code="CHAT_INPUT_TOO_LARGE", http_status=413, data={"field": field})
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not pattern.fullmatch(item.strip()):
            raise ChatError("Invalid public identifier.", code="CHAT_INVALID_IDENTIFIER", data={"field": field})
        result.append(item.strip())
    return list(dict.fromkeys(result))


def validate_public_kwargs(kwargs: Mapping[str, Any], spec: EndpointSpec) -> dict[str, Any]:
    clean = {k: v for k, v in dict(kwargs or {}).items() if k not in TRANSPORT_FIELDS}
    _validate_shape(clean)

    unknown = sorted(set(clean) - set(spec.allowed_fields))
    if unknown:
        raise ChatError(
            "Unsupported Chat request field.",
            code="CHAT_UNKNOWN_FIELD",
            data={"fields": unknown[:10]},
        )

    for group in spec.aliases:
        supplied = [field for field in group if clean.get(field) not in (None, "", [], ())]
        if len(supplied) > 1:
            canonical = []
            for field in supplied:
                value = clean[field]
                canonical.append(json.dumps(value, sort_keys=True, default=str) if isinstance(value, (dict, list)) else str(value).strip())
            if len(set(canonical)) > 1:
                raise ChatError(
                    "Conflicting Chat request aliases.",
                    code="CHAT_ALIAS_CONFLICT",
                    data={"fields": supplied},
                )

    for field, pattern in spec.id_fields:
        value = clean.get(field)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
            raise ChatError("Invalid public identifier.", code="CHAT_INVALID_IDENTIFIER", data={"field": field})
        clean[field] = value.strip()

    if "user" in clean and clean.get("user") is not None:
        clean["user"] = normalize_text(clean["user"], field="user", max_length=254, required=True)
    if "content" in clean and clean.get("content") is not None:
        clean["content"] = normalize_text(clean["content"], field="content", max_length=MAX_CONTENT_LENGTH)
    if "message" in clean and clean.get("message") is not None:
        clean["message"] = normalize_text(clean["message"], field="message", max_length=MAX_CONTENT_LENGTH)
    if "emoji" in clean and clean.get("emoji") is not None:
        clean["emoji"] = normalize_text(clean["emoji"], field="emoji", max_length=MAX_EMOJI_LENGTH)
    for field in ("source_language", "target_language"):
        if field in clean and clean.get(field) is not None:
            clean[field] = normalize_text(clean[field], field=field, max_length=MAX_TRANSLATION_LANGUAGE_LENGTH)
    if "idempotency_key" in clean and clean.get("idempotency_key") is not None:
        clean["idempotency_key"] = normalize_text(
            clean["idempotency_key"], field="idempotency_key", max_length=MAX_IDEMPOTENCY_KEY_LENGTH
        )

    if "attachments" in clean:
        attachments = clean.get("attachments") or []
        if not isinstance(attachments, list):
            raise ChatError("attachments must be a list.", data={"field": "attachments"})
        if len(attachments) > MAX_ATTACHMENTS:
            raise ChatError("Too many attachments.", code="CHAT_INPUT_TOO_LARGE", http_status=413)
        allowed_attachment_fields = frozenset({"media", "media_id", "id", "file_type", "type", "file"})
        for attachment in attachments:
            if not isinstance(attachment, dict) or len(attachment) > 8:
                raise ChatError("Invalid attachment payload.", data={"field": "attachments"})
            unknown_attachment = sorted(set(attachment) - allowed_attachment_fields)
            if unknown_attachment:
                raise ChatError(
                    "Unsupported attachment field.",
                    code="CHAT_UNKNOWN_FIELD",
                    data={"fields": unknown_attachment[:8]},
                )
            media_values = [
                attachment.get(field)
                for field in ("media", "media_id", "id")
                if attachment.get(field) not in (None, "")
            ]
            if media_values:
                canonical = [str(value).strip() for value in media_values]
                if len(set(canonical)) > 1:
                    raise ChatError(
                        "Conflicting attachment aliases.",
                        code="CHAT_ALIAS_CONFLICT",
                        data={"fields": ["media", "media_id", "id"]},
                    )
                if not MEDIA_ID_RE.fullmatch(canonical[0]):
                    raise ChatError(
                        "Invalid public identifier.",
                        code="CHAT_INVALID_IDENTIFIER",
                        data={"field": "attachments.media_id"},
                    )
        clean["attachments"] = attachments

    if "message_ids" in clean:
        clean["message_ids"] = _normalize_id_list(
            clean.get("message_ids"), field="message_ids", pattern=MESSAGE_ID_RE, maximum=MAX_MULTI_MESSAGE_IDS
        )
    if "target_conversation_ids" in clean:
        clean["target_conversation_ids"] = _normalize_id_list(
            clean.get("target_conversation_ids"),
            field="target_conversation_ids",
            pattern=CONVERSATION_ID_RE,
            maximum=MAX_FORWARD_TARGETS,
        )

    for field, minimum, maximum in (("limit", 1, 100), ("offset", 0, 10000)):
        if field not in clean or clean.get(field) in (None, ""):
            continue
        value = clean[field]
        if isinstance(value, bool):
            raise ChatError("Invalid pagination value.", data={"field": field})
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ChatError("Invalid pagination value.", data={"field": field}) from exc
        if parsed < minimum or parsed > maximum:
            raise ChatError("Invalid pagination value.", data={"field": field})
        clean[field] = parsed


    if "delete_scope" in clean and clean.get("delete_scope") not in (None, ""):
        scope = normalize_text(clean["delete_scope"], field="delete_scope", max_length=16).lower()
        if scope not in {"me", "everyone"}:
            raise ChatError("Invalid delete scope.", data={"field": "delete_scope"})
        clean["delete_scope"] = scope

    for field in ("is_typing", "force_refresh"):
        if field not in clean or clean.get(field) in (None, ""):
            continue
        value = clean[field]
        if value in (True, 1, "1", "true", "True"):
            clean[field] = 1
        elif value in (False, 0, "0", "false", "False"):
            clean[field] = 0
        else:
            raise ChatError("Invalid boolean value.", data={"field": field})

    return clean
