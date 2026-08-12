"""Strict Activity Center request validation."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .constants import (
    ACTIVITY_GROUP_MAX_LEN,
    ACTIVITY_ID_MAX_LEN,
    ACTIVITY_TYPE_MAX_LEN,
    DEFAULT_ACTIVITY_LIMIT,
    MAX_ACTIVITY_LIMIT,
    MAX_ACTIVITY_START,
    VALID_ACTIVITY_GROUPS,
    VALID_ACTIVITY_TYPES,
)
from .errors import ActivityValidationError

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def ensure_known_fields(payload: dict[str, Any], allowed: Iterable[str]) -> None:
    unknown = sorted(str(key) for key in payload if key not in set(allowed))
    if unknown:
        raise ActivityValidationError(
            f"Unsupported activity fields: {', '.join(unknown[:10])}."
        )


def _optional_text(value: Any, *, field: str, max_length: int) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ActivityValidationError(f"{field} must be text.")
    normalized = value.strip()
    if _CONTROL_RE.search(normalized) or len(normalized) > max_length:
        raise ActivityValidationError(f"Invalid {field.lower()}.")
    return normalized


def aliased_text(
    payload: dict[str, Any],
    primary: str,
    alias: str,
    *,
    field: str,
    max_length: int,
) -> str:
    first = _optional_text(payload.get(primary), field=field, max_length=max_length)
    second = _optional_text(payload.get(alias), field=field, max_length=max_length)
    if first and second and first != second:
        raise ActivityValidationError(f"Conflicting {field.lower()} aliases.")
    return first or second


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
        raise ActivityValidationError(
            f"Activity limit must be between 1 and {MAX_ACTIVITY_LIMIT}."
        )
    return limit


def normalize_start(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise ActivityValidationError("Invalid activity offset.")
    try:
        start = int(value)
    except (TypeError, ValueError):
        raise ActivityValidationError("Invalid activity offset.") from None
    if start < 0 or start > MAX_ACTIVITY_START:
        raise ActivityValidationError("Invalid activity offset.")
    return start


def normalize_group_filter(payload: dict[str, Any]) -> str:
    group = aliased_text(
        payload,
        "group",
        "activity_group",
        field="Activity group",
        max_length=ACTIVITY_GROUP_MAX_LEN,
    )
    if not group:
        return ""
    if group not in VALID_ACTIVITY_GROUPS:
        raise ActivityValidationError("Invalid activity group.")
    return group


def normalize_type_filter(payload: dict[str, Any]) -> str:
    activity_type = aliased_text(
        payload,
        "type",
        "activity_type",
        field="Activity type",
        max_length=ACTIVITY_TYPE_MAX_LEN,
    )
    if not activity_type:
        return ""
    if activity_type not in VALID_ACTIVITY_TYPES:
        raise ActivityValidationError("Invalid activity type.")
    return activity_type


def normalize_activity_id(payload: dict[str, Any]) -> str:
    activity_id = aliased_text(
        payload,
        "activity_id",
        "id",
        field="Activity ID",
        max_length=ACTIVITY_ID_MAX_LEN,
    )
    if not activity_id:
        raise ActivityValidationError("Activity ID is required.")
    return activity_id
