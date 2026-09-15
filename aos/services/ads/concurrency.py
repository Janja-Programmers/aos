"""Optimistic mutation checks performed while holding the aggregate row lock."""
from __future__ import annotations

from typing import Any

from .errors import AdsConflictError, AdsValidationError


def normalize_version(value: Any, *, required: bool = True) -> str:
    if value in (None, ""):
        if required:
            raise AdsValidationError("version is required.", code="INVALID_AD_INPUT")
        return ""
    if not isinstance(value, str):
        raise AdsValidationError("Invalid version.", code="INVALID_AD_INPUT")
    clean = value.strip()
    if not clean or len(clean) > 64:
        raise AdsValidationError("Invalid version.", code="INVALID_AD_INPUT")
    return clean


def assert_version(doc: Any, expected: Any) -> None:
    wanted = normalize_version(expected)
    actual = str(getattr(doc, "modified", "") or "").strip()
    if actual != wanted:
        raise AdsConflictError("The ad changed since it was loaded.", code="AD_CONFLICT")
