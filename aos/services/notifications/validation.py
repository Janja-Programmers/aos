"""Shared strict input validation for Notification public APIs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class NotificationInputError(ValueError):
    """Raised for malformed public Notification endpoint input."""


def reject_unknown_fields(kwargs: Mapping[str, Any], *, allowed: set[str] | frozenset[str]) -> None:
    unknown = sorted(str(key) for key in kwargs if str(key) not in allowed)
    if unknown:
        raise NotificationInputError("Unsupported request field.")


def require_identifier(value: Any, *, field: str, max_length: int = 180) -> str:
    text = str(value or "").strip()
    if not text:
        raise NotificationInputError(f"{field} is required.")
    if len(text) > max_length or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise NotificationInputError(f"Invalid {field}.")
    return text
