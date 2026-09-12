"""Strict Social request and cursor validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from typing import Any, Iterable

import frappe

from aos.services.accounts.identity import normalize_public_account_id, resolve_account_reference

from .constants import (
    BLOCK_REASON_MAX_LENGTH,
    CURSOR_MAX_LENGTH,
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    SEARCH_MAX_LENGTH,
    SEARCH_MIN_LENGTH,
)
from .errors import SocialCursorError, SocialValidationError


def ensure_known_fields(payload: dict[str, Any], allowed: Iterable[str]) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise SocialValidationError(
            "Unsupported Social request field.",
            code="SOCIAL_UNKNOWN_FIELD",
            data={"fields": unknown[:10]},
        )


def normalize_target(payload: dict[str, Any]) -> str:
    raw = payload.get("account_id")
    if not isinstance(raw, str):
        if raw in (None, ""):
            raise SocialValidationError("Target account is required.", code="SOCIAL_TARGET_REQUIRED")
        raise SocialValidationError("Invalid public account ID.", code="SOCIAL_INVALID_ACCOUNT_ID")
    account_id = normalize_public_account_id(raw)
    if not account_id:
        raise SocialValidationError("Invalid public account ID.", code="SOCIAL_INVALID_ACCOUNT_ID")
    resolved = resolve_account_reference(account_id)
    if not resolved:
        raise SocialValidationError("Account does not exist.", code="SOCIAL_ACCOUNT_NOT_FOUND", http_status=404)
    return str(resolved)


def normalize_reason(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SocialValidationError("Block reason must be text.", code="SOCIAL_INVALID_REASON")
    reason = re.sub(r"\s+", " ", value.strip())
    if len(reason) > BLOCK_REASON_MAX_LENGTH:
        raise SocialValidationError("Block reason is too long.", code="SOCIAL_INVALID_REASON")
    return reason


def normalize_search(value: Any, *, required: bool) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SocialValidationError("Search query must be text.", code="SOCIAL_INVALID_SEARCH")
    query = re.sub(r"\s+", " ", value.strip())
    if not query and not required:
        return ""
    if len(query) < SEARCH_MIN_LENGTH or len(query) > SEARCH_MAX_LENGTH:
        raise SocialValidationError(
            f"Search query must be between {SEARCH_MIN_LENGTH} and {SEARCH_MAX_LENGTH} characters.",
            code="SOCIAL_INVALID_SEARCH",
        )
    return query


def normalize_limit(value: Any, *, default: int = DEFAULT_LIST_LIMIT, maximum: int = MAX_LIST_LIMIT) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise SocialValidationError("Invalid list limit.", code="SOCIAL_INVALID_LIMIT")
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise SocialValidationError("Invalid list limit.", code="SOCIAL_INVALID_LIMIT") from None
    if limit < 1 or limit > maximum:
        raise SocialValidationError(f"List limit must be between 1 and {maximum}.", code="SOCIAL_INVALID_LIMIT")
    return limit


def _secret() -> bytes:
    config: dict[str, Any] = {}
    try:
        config.update(dict(getattr(frappe.local, "conf", {}) or {}))
    except Exception:
        pass
    if not config:
        try:
            config.update(dict(frappe.get_site_config() or {}))
        except Exception:
            pass
    value = str(config.get("encryption_key") or config.get("db_password") or "").strip()
    if not value:
        raise SocialCursorError("Social pagination is temporarily unavailable.")
    return value.encode("utf-8")


def encode_cursor(*, kind: str, values: dict[str, Any]) -> str:
    body = json.dumps({"v": 1, "k": kind, "d": values}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(_secret(), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + signature).decode("ascii").rstrip("=")


def decode_cursor(value: Any, *, kind: str) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > CURSOR_MAX_LENGTH:
        raise SocialCursorError("Invalid Social pagination cursor.")
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(raw) <= 32:
            raise ValueError
        body, signature = raw[:-32], raw[-32:]
        expected = hmac.new(_secret(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(body.decode("utf-8"))
        if payload.get("v") != 1 or payload.get("k") != kind or not isinstance(payload.get("d"), dict):
            raise ValueError
        return dict(payload["d"])
    except Exception:
        raise SocialCursorError("Invalid Social pagination cursor.") from None


def pagination(payload: dict[str, Any], *, kind: str, maximum: int = MAX_LIST_LIMIT) -> tuple[int, dict[str, Any] | None]:
    return normalize_limit(payload.get("limit"), maximum=maximum), decode_cursor(payload.get("cursor"), kind=kind)
