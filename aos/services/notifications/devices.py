"""Push-device validation and privacy-safe token helpers."""

from __future__ import annotations

import hashlib
import re
from typing import Any


class PushDeviceValidationError(ValueError):
    """Raised when a push-device registration is malformed."""


VALID_DEVICE_TYPES = frozenset({"android", "ios", "web"})
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
