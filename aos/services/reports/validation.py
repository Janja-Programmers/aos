"""Strict normalization for the existing Report APIs and DocTypes."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

import frappe

from .constants import TRANSPORT_FIELDS
from .errors import ReportValidationError


def strip_transport_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    return {str(key): value for key, value in raw.items() if str(key) not in TRANSPORT_FIELDS}


def ensure_known_fields(payload: dict[str, Any], allowed: Iterable[str]) -> None:
    clean = strip_transport_fields(payload)
    allowed_set = set(allowed)
    unknown = sorted(key for key in clean if key not in allowed_set)
    if unknown:
        raise ReportValidationError(f"Unsupported report fields: {', '.join(unknown)}.")


def clean_text(value: Any, *, field: str, max_length: int, required: bool = False, multiline: bool = False) -> str:
    if isinstance(value, (dict, list, tuple, set, bool)):
        raise ReportValidationError(f"Invalid {field.replace('_', ' ')}.")
    text = unicodedata.normalize("NFC", str(value or ""))
    if "\x00" in text:
        raise ReportValidationError(f"Invalid {field.replace('_', ' ')}.")
    for char in text:
        if unicodedata.category(char) == "Cc" and char not in {"\n", "\r", "\t"}:
            raise ReportValidationError(f"Invalid {field.replace('_', ' ')}.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text.strip())
    text = re.sub(r"\n{3,}", "\n\n", text) if multiline else re.sub(r"\s+", " ", text)
    if required and not text:
        raise ReportValidationError(f"{field.replace('_', ' ').title()} is required.")
    if len(text) > max_length:
        raise ReportValidationError(f"{field.replace('_', ' ').title()} is too long. Maximum is {max_length} characters.")
    return text


def normalize_reason(value: Any) -> str:
    return clean_text(value, field="reason", max_length=140, required=True)


def normalize_details(value: Any, *, max_length: int) -> str:
    return clean_text(value, field="details", max_length=max_length, multiline=True)


def validate_active_reason(reason: str) -> str:
    row = frappe.db.get_value("AOS Report Reason", reason, ["name", "is_active"], as_dict=True)
    if not row:
        raise ReportValidationError("Invalid report reason.")
    if not int(row.is_active or 0):
        raise ReportValidationError("Selected report reason is inactive.")
    return str(row.name)



def aliased_value(payload: dict[str, Any], primary: str, alias: str) -> Any:
    primary_value = payload.get(primary)
    alias_value = payload.get(alias)
    p = str(primary_value or "").strip()
    a = str(alias_value or "").strip()
    if p and a and p != a:
        raise ReportValidationError(f"Conflicting {primary.replace('_', ' ')} values.")
    return primary_value if p else alias_value


def normalize_optional_bool(value: Any, *, field: str) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ReportValidationError(f"Invalid {field.replace('_', ' ')}.")


def truthy(value: Any) -> bool:
    """Compatibility alias for legacy callers."""
    return normalize_optional_bool(value, field="value")
