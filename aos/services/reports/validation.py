"""Strict request and master-data validation for Reports."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

import frappe
from frappe.utils import strip_html

from .constants import PUBLIC_REPORT_TARGETS, REPORT_REASON_TARGETS, TRANSPORT_FIELDS
from .errors import ReportReasonError, ReportReasonNotAllowedError, ReportValidationError

_REASON_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def strip_transport_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    return {str(key): value for key, value in raw.items() if str(key) not in TRANSPORT_FIELDS}


def ensure_known_fields(payload: dict[str, Any], allowed: Iterable[str]) -> None:
    clean = strip_transport_fields(payload)
    allowed_set = set(allowed)
    unknown = sorted(key for key in clean if key not in allowed_set)
    if unknown:
        raise ReportValidationError(f"Unsupported report fields: {', '.join(unknown)}.")


def clean_text(
    value: Any,
    *,
    field: str,
    max_length: int,
    required: bool = False,
    multiline: bool = False,
    reject_html: bool = False,
) -> str:
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
    if reject_html and strip_html(text) != text:
        raise ReportValidationError(f"Invalid {field.replace('_', ' ')}.")
    if required and not text:
        raise ReportValidationError(f"{field.replace('_', ' ').title()} is required.")
    if len(text) > max_length:
        raise ReportValidationError(
            f"{field.replace('_', ' ').title()} is too long. Maximum is {max_length} characters."
        )
    return text


def normalize_reason_id(value: Any) -> str:
    reason_id = clean_text(value, field="reason_id", max_length=64, required=True).lower()
    if not _REASON_ID_RE.fullmatch(reason_id):
        raise ReportReasonError("Invalid report reason.")
    return reason_id


def normalize_details(value: Any, *, max_length: int) -> str:
    return clean_text(
        value,
        field="details",
        max_length=max_length,
        multiline=True,
        reject_html=True,
    )


def normalize_public_target_type(value: Any) -> tuple[str, str]:
    target_type = clean_text(value, field="target_type", max_length=20, required=True).lower()
    canonical = PUBLIC_REPORT_TARGETS.get(target_type)
    if not canonical:
        raise ReportValidationError("Unsupported report target type.")
    return target_type, canonical


def validate_reason_for_target(reason_id: Any, target_type: str) -> str:
    reason = normalize_reason_id(reason_id)
    if target_type not in REPORT_REASON_TARGETS:
        raise ReportValidationError("Unsupported report target type.")
    row = frappe.db.get_value(
        "AOS Report Reason",
        reason,
        ["name", "is_enabled"],
        as_dict=True,
    )
    if not row or not int(row.is_enabled or 0):
        raise ReportReasonError("Invalid report reason.")
    allowed = frappe.db.exists(
        "AOS Report Reason Target",
        {
            "parent": reason,
            "parenttype": "AOS Report Reason",
            "parentfield": "allowed_targets",
            "target_type": target_type,
        },
    )
    if not allowed:
        raise ReportReasonNotAllowedError("Report reason is not valid for this target type.")
    return str(row.name)


def validate_existing_reason(reason_id: Any) -> str:
    reason = clean_text(reason_id, field="reason", max_length=140, required=True)
    if not frappe.db.exists("AOS Report Reason", reason):
        raise ReportReasonError("Invalid report reason.")
    return reason
