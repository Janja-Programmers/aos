"""Strict public request validation for AOS Chat v1 endpoints."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Pattern

from aos.services.live.validation import LIVE_ID_RE
from aos.services.media.identifiers import MEDIA_ID_RE
from aos.services.shorts.identity import SHORT_ID_RE

from .errors import ChatError
from .identifiers import CONVERSATION_ID_RE, MESSAGE_ID_RE

# Ads owns this public identifier.  Marketplace Discovery generates exactly
# ``ad_`` plus 24 URL-safe base64 characters from 18 random bytes.
AD_ID_RE = re.compile(r"^ad_[A-Za-z0-9_-]{24}$")
PUBLIC_ACCOUNT_ID_RE = re.compile(r"^ACC-[A-Z2-7]{20}$")

MAX_REQUEST_BYTES = 64 * 1024
MAX_CONTENT_LENGTH = 4000
MAX_TRANSLATION_LANGUAGE_LENGTH = 32
MAX_EMOJI_LENGTH = 16
MAX_IDEMPOTENCY_KEY_LENGTH = 128
MAX_CURSOR_LENGTH = 512
MAX_ATTACHMENTS = 10
MAX_MULTI_MESSAGE_IDS = 100
MAX_FORWARD_TARGETS = 20
MAX_GROUP_MEMBER_INPUT = 255
MAX_GROUP_TITLE_LENGTH = 140
MAX_LOCK_SECRET_LENGTH = 64
MAX_LOCK_TOKEN_LENGTH = 256
MAX_JSON_DEPTH = 8

TRANSPORT_FIELDS = frozenset({"cmd"})


@dataclass(frozen=True)
class EndpointSpec:
    allowed_fields: frozenset[str]
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
        values = [_depth(key, current + 1) for key in value]
        values.extend(_depth(item, current + 1) for item in value.values())
        return max(values)
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
    if not isinstance(value, list):
        raise ChatError(f"{field} must be a list.", data={"field": field})
    if len(value) > maximum:
        raise ChatError("Too many identifiers.", code="CHAT_INPUT_TOO_LARGE", http_status=413, data={"field": field})
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not pattern.fullmatch(item.strip()):
            raise ChatError("Invalid public identifier.", code="CHAT_INVALID_IDENTIFIER", data={"field": field})
        result.append(item.strip())
    return list(dict.fromkeys(result))


def _normalize_bool(value: Any, *, field: str) -> int:
    if value in (True, 1, "1", "true", "True"):
        return 1
    if value in (False, 0, "0", "false", "False"):
        return 0
    raise ChatError("Invalid boolean value.", data={"field": field})


def validate_public_kwargs(kwargs: Mapping[str, Any], spec: EndpointSpec) -> dict[str, Any]:
    clean = {key: value for key, value in dict(kwargs or {}).items() if key not in TRANSPORT_FIELDS}
    _validate_shape(clean)

    unknown = sorted(set(clean) - set(spec.allowed_fields))
    if unknown:
        raise ChatError(
            "Unsupported Chat request field.",
            code="CHAT_UNKNOWN_FIELD",
            data={"fields": unknown[:10]},
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
    if "title" in clean and clean.get("title") is not None:
        clean["title"] = normalize_text(clean["title"], field="title", max_length=MAX_GROUP_TITLE_LENGTH, required=True)
    for field in ("secret", "current_secret"):
        if field in clean and clean.get(field) is not None:
            clean[field] = normalize_text(clean[field], field=field, max_length=MAX_LOCK_SECRET_LENGTH, required=True)
    if "lock_token" in clean and clean.get("lock_token") is not None:
        clean["lock_token"] = normalize_text(clean["lock_token"], field="lock_token", max_length=MAX_LOCK_TOKEN_LENGTH, required=True)
    if "emoji" in clean and clean.get("emoji") is not None:
        clean["emoji"] = normalize_text(clean["emoji"], field="emoji", max_length=MAX_EMOJI_LENGTH)
    for field in ("source_language", "target_language"):
        if field in clean and clean.get(field) is not None:
            clean[field] = normalize_text(clean[field], field=field, max_length=MAX_TRANSLATION_LANGUAGE_LENGTH)
    if "idempotency_key" in clean and clean.get("idempotency_key") is not None:
        clean["idempotency_key"] = normalize_text(
            clean["idempotency_key"], field="idempotency_key", max_length=MAX_IDEMPOTENCY_KEY_LENGTH
        )
    if "cursor" in clean and clean.get("cursor") is not None:
        clean["cursor"] = normalize_text(clean["cursor"], field="cursor", max_length=MAX_CURSOR_LENGTH)

    if "attachments" in clean:
        attachments = clean.get("attachments") or []
        if not isinstance(attachments, list):
            raise ChatError("attachments must be a list.", data={"field": "attachments"})
        if len(attachments) > MAX_ATTACHMENTS:
            raise ChatError("Too many attachments.", code="CHAT_INPUT_TOO_LARGE", http_status=413)
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for attachment in attachments:
            if not isinstance(attachment, dict):
                raise ChatError("Invalid attachment payload.", data={"field": "attachments"})
            unknown_attachment = sorted(set(attachment) - {"media_id"})
            if unknown_attachment:
                raise ChatError(
                    "Unsupported attachment field.",
                    code="CHAT_UNKNOWN_FIELD",
                    data={"fields": unknown_attachment[:8]},
                )
            media_id = attachment.get("media_id")
            if not isinstance(media_id, str) or not MEDIA_ID_RE.fullmatch(media_id.strip()):
                raise ChatError(
                    "Invalid public identifier.",
                    code="CHAT_INVALID_IDENTIFIER",
                    data={"field": "attachments.media_id"},
                )
            media_id = media_id.strip()
            if media_id in seen:
                raise ChatError("Duplicate attachment media_id.", code="CHAT_CONFLICT", http_status=409)
            seen.add(media_id)
            normalized.append({"media_id": media_id})
        clean["attachments"] = normalized

    if "message_ids" in clean:
        clean["message_ids"] = _normalize_id_list(
            clean.get("message_ids"), field="message_ids", pattern=MESSAGE_ID_RE, maximum=MAX_MULTI_MESSAGE_IDS
        )
    if "participant_ids" in clean:
        clean["participant_ids"] = _normalize_id_list(
            clean.get("participant_ids"), field="participant_ids", pattern=PUBLIC_ACCOUNT_ID_RE, maximum=MAX_GROUP_MEMBER_INPUT
        )
    if "target_conversation_ids" in clean:
        clean["target_conversation_ids"] = _normalize_id_list(
            clean.get("target_conversation_ids"),
            field="target_conversation_ids",
            pattern=CONVERSATION_ID_RE,
            maximum=MAX_FORWARD_TARGETS,
        )

    if "limit" in clean and clean.get("limit") not in (None, ""):
        value = clean["limit"]
        if isinstance(value, bool):
            raise ChatError("Invalid pagination value.", data={"field": "limit"})
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ChatError("Invalid pagination value.", data={"field": "limit"}) from exc
        if parsed < 1 or parsed > 100:
            raise ChatError("Invalid pagination value.", data={"field": "limit"})
        clean["limit"] = parsed

    if "delete_scope" in clean and clean.get("delete_scope") not in (None, ""):
        scope = normalize_text(clean["delete_scope"], field="delete_scope", max_length=16).lower()
        if scope not in {"me", "everyone"}:
            raise ChatError("Invalid delete scope.", data={"field": "delete_scope"})
        clean["delete_scope"] = scope

    if "role" in clean and clean.get("role") not in (None, ""):
        role = normalize_text(clean["role"], field="role", max_length=16).lower()
        if role not in {"admin", "member"}:
            raise ChatError("Invalid group role.", data={"field": "role"})
        clean["role"] = role

    for field in ("is_typing", "force_refresh", "starred", "locked", "hide_locked_chats", "remove_avatar"):

        if field in clean and clean.get(field) not in (None, ""):
            clean[field] = _normalize_bool(clean[field], field=field)

    _validate_shape(clean)
    return clean
