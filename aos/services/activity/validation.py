"""Strict Activity Center public-request validation."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .constants import (
    ACTIVITY_GROUP_MAX_LEN,
    ACTIVITY_TYPE_MAX_LEN,
    DEFAULT_ACTIVITY_LIMIT,
    MAX_ACTIVITY_LIMIT,
    MAX_CURSOR_LENGTH,
    EVENT_SPECS,
    VALID_ACTIVITY_GROUPS,
    VALID_ACTIVITY_TYPES,
)
from .errors import ActivityValidationError
from .identity import normalize_activity_id as _normalize_public_activity_id

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def ensure_known_fields(payload: dict[str, Any], allowed: Iterable[str]) -> None:
    allowed_set = set(allowed)
    unknown = sorted(str(key) for key in payload if key not in allowed_set)
    if unknown:
        raise ActivityValidationError(f"Unsupported activity fields: {', '.join(unknown[:10])}.")


def _optional_text(value: Any, *, field: str, max_length: int) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ActivityValidationError(f"{field} must be text.")
    normalized = value.strip()
    if _CONTROL_RE.search(normalized) or len(normalized) > max_length:
        raise ActivityValidationError(f"Invalid {field.lower()}.")
    return normalized


def normalize_limit(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_ACTIVITY_LIMIT
    if isinstance(value, bool):
        raise ActivityValidationError("Invalid activity limit.")
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise ActivityValidationError("Invalid activity limit.") from None
    if limit < 1 or limit > MAX_ACTIVITY_LIMIT:
        raise ActivityValidationError(f"Activity limit must be between 1 and {MAX_ACTIVITY_LIMIT}.")
    return limit


def normalize_cursor(value: Any) -> str:
    return _optional_text(value, field="Activity cursor", max_length=MAX_CURSOR_LENGTH)


def normalize_group_filter(payload: dict[str, Any]) -> str:
    group = _optional_text(payload.get("group"), field="Activity group", max_length=ACTIVITY_GROUP_MAX_LEN)
    if group and group not in VALID_ACTIVITY_GROUPS:
        raise ActivityValidationError("Invalid activity group.")
    return group


def normalize_type_filter(payload: dict[str, Any]) -> str:
    activity_type = _optional_text(payload.get("type"), field="Activity type", max_length=ACTIVITY_TYPE_MAX_LEN)
    if activity_type and activity_type not in VALID_ACTIVITY_TYPES:
        raise ActivityValidationError("Invalid activity type.")
    return activity_type


def validate_filter_pair(*, group: str, activity_type: str) -> None:
    if group and activity_type and str(EVENT_SPECS[activity_type]["group"]) != group:
        raise ActivityValidationError("Activity type does not belong to the selected group.")


def normalize_activity_id(payload: dict[str, Any]) -> str:
    value = _optional_text(payload.get("activity_id"), field="Activity ID", max_length=64)
    activity_id = _normalize_public_activity_id(value)
    if not activity_id:
        raise ActivityValidationError("Invalid activity ID.")
    return activity_id
