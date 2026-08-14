"""Push-device validation and privacy-safe token helpers."""

from __future__ import annotations

import hashlib
import re
from typing import Any


class PushDeviceValidationError(ValueError):
    """Raised when a push-device registration is malformed."""


VALID_DEVICE_TYPES = frozenset({"android", "ios", "web"})
VALID_REGISTRATION_KINDS = frozenset({"token", "fid"})
MIN_PUSH_TOKEN_LENGTH = 20
MAX_PUSH_TOKEN_LENGTH = 4096
MAX_DEVICE_ID_LENGTH = 180
_TOKEN_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+\-]{0,179}$")


def normalize_push_token(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        raise PushDeviceValidationError("token is required.")
    if len(token) < MIN_PUSH_TOKEN_LENGTH or len(token) > MAX_PUSH_TOKEN_LENGTH:
        raise PushDeviceValidationError("Invalid token length.")
    if _TOKEN_CONTROL_CHARS.search(token) or any(ch.isspace() for ch in token):
        raise PushDeviceValidationError("Invalid token format.")
    return token


def normalize_registration_kind(value: Any) -> str:
    """Normalize the Firebase target identity kind.

    Legacy AOS clients upload FCM registration tokens and do not send this
    field, so an omitted value intentionally defaults to ``token``. Newer
    clients may explicitly register a Firebase Installation ID (FID).
    """
    registration_kind = str(value or "token").strip().lower()
    if registration_kind not in VALID_REGISTRATION_KINDS:
        raise PushDeviceValidationError("Invalid registration_kind.")
    return registration_kind


def normalize_device_type(value: Any) -> str:
    device_type = str(value or "").strip().lower()
    if not device_type:
        raise PushDeviceValidationError("device_type is required.")
    if device_type not in VALID_DEVICE_TYPES:
        raise PushDeviceValidationError("Invalid device_type.")
    return device_type


def normalize_device_id(value: Any) -> str:
    device_id = str(value or "").strip()
    if not device_id:
        return ""
    if len(device_id) > MAX_DEVICE_ID_LENGTH or not _DEVICE_ID_PATTERN.fullmatch(device_id):
        raise PushDeviceValidationError("Invalid device_id.")
    return device_id


def get_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_fingerprint(*, token: str | None = None, token_hash: str | None = None) -> str:
    """Return a non-reversible diagnostic identity; never return the raw token."""
    digest = str(token_hash or "").strip().lower()
    if not digest and token:
        digest = get_token_hash(token)
    return digest[:12]
