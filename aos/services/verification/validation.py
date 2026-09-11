"""Strict normalization for Verification submission payloads."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

from aos.services.accounts.validation import validate_phone

from .constants import (
    BUSINESS_TYPES,
    DOCUMENT_FIELDS,
    MAX_BUSINESS_ADDRESS_LENGTH,
    MAX_BUSINESS_CATEGORY_LENGTH,
    MAX_BUSINESS_EMAIL_LENGTH,
    MAX_BUSINESS_NAME_LENGTH,
    MAX_BUSINESS_WEBSITE_LENGTH,
    MAX_DOCUMENT_NUMBER_LENGTH,
    MAX_DOCUMENT_TYPE_LENGTH,
    MAX_DOCUMENTS,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_LEGAL_NAME_LENGTH,
    MIN_IDEMPOTENCY_KEY_LENGTH,
    SUBMIT_FIELDS,
    TRANSPORT_FIELDS,
    TYPE_INDIVIDUAL,
    VERIFICATION_TYPES,
)
from .errors import VerificationValidationError

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MEDIA_ID_RE = re.compile(r"^MEDIA-[A-Za-z0-9._-]{1,96}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$")


def _text(
    value: Any,
    *,
    field: str,
    max_length: int,
    required: bool = False,
    multiline: bool = False,
) -> str:
    if isinstance(value, (dict, list, tuple, set, bool)):
        raise VerificationValidationError(f"Invalid {field.replace('_', ' ')}.")
    text = unicodedata.normalize("NFC", str(value or ""))
    if "\x00" in text:
        raise VerificationValidationError(f"Invalid {field.replace('_', ' ')}.")
    for char in text:
        if unicodedata.category(char) == "Cc" and char not in {"\n", "\r", "\t"}:
            raise VerificationValidationError(f"Invalid {field.replace('_', ' ')}.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text.strip())
    text = re.sub(r"\n{3,}", "\n\n", text) if multiline else re.sub(r"\s+", " ", text)
    if required and not text:
        raise VerificationValidationError(f"{field.replace('_', ' ').title()} is required.")
    if len(text) > max_length:
        raise VerificationValidationError(f"{field.replace('_', ' ').title()} is too long.")
    return text


def strip_transport_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    return {str(key): value for key, value in raw.items() if str(key) not in TRANSPORT_FIELDS}


def ensure_known_submit_fields(payload: dict[str, Any]) -> None:
    unknown = sorted(key for key in payload if key not in SUBMIT_FIELDS)
    if unknown:
        raise VerificationValidationError(
            f"Unsupported verification fields: {', '.join(unknown)}.",
            code="VERIFICATION_UNKNOWN_FIELD",
        )


def _verification_type(value: Any) -> str:
    clean = _text(value, field="verification_type", max_length=32, required=True)
    if clean not in VERIFICATION_TYPES:
        raise VerificationValidationError(
            "Invalid verification type.", code="VERIFICATION_INVALID_TYPE"
        )
    return clean


def _email(value: Any) -> str:
    text = _text(
        value,
        field="business_email",
        max_length=MAX_BUSINESS_EMAIL_LENGTH,
        required=True,
    ).lower()
    if not _EMAIL_RE.fullmatch(text):
        raise VerificationValidationError("Invalid business email.")
    return text


def _website(value: Any) -> str:
    text = _text(value, field="business_website", max_length=MAX_BUSINESS_WEBSITE_LENGTH)
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise VerificationValidationError("Invalid business website.")
    return text


def _date(value: Any, *, field: str) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise VerificationValidationError(f"Invalid {field.replace('_', ' ')}.") from exc


def normalize_document_payload(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise VerificationValidationError("Invalid verification document payload.")
    unknown = sorted(str(key) for key in row if str(key) not in DOCUMENT_FIELDS)
    if unknown:
        raise VerificationValidationError(
            f"Unsupported verification document fields: {', '.join(unknown)}.",
            code="VERIFICATION_UNKNOWN_FIELD",
        )
    media_id = str(row.get("media_id") or "").strip()
    if not media_id or len(media_id) > 128 or not _MEDIA_ID_RE.fullmatch(media_id):
        raise VerificationValidationError(
            "Invalid verification document media.", code="VERIFICATION_INVALID_DOCUMENT"
        )
    issue_date = _date(row.get("issue_date"), field="issue_date")
    expiry_date = _date(row.get("expiry_date"), field="expiry_date")
    if issue_date and expiry_date and issue_date > expiry_date:
        raise VerificationValidationError("Document issue date cannot be after expiry date.")
    return {
        "document_type": _text(
            row.get("document_type"),
            field="document_type",
            max_length=MAX_DOCUMENT_TYPE_LENGTH,
            required=True,
        ),
        "document_number": _text(
            row.get("document_number"),
            field="document_number",
            max_length=MAX_DOCUMENT_NUMBER_LENGTH,
        ),
        "issue_date": issue_date,
        "expiry_date": expiry_date,
        "media_id": media_id,
    }


def _documents(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise VerificationValidationError("Verification documents are required.")
    if len(value) > MAX_DOCUMENTS:
        raise VerificationValidationError(
            f"A maximum of {MAX_DOCUMENTS} verification documents is allowed.",
            code="VERIFICATION_INVALID_DOCUMENT",
        )
    normalized = [normalize_document_payload(row) for row in value]
    media_ids = [row["media_id"] for row in normalized]
    if len(set(media_ids)) != len(media_ids):
        raise VerificationValidationError(
            "Duplicate verification document media is not allowed.",
            code="VERIFICATION_DUPLICATE_DOCUMENT",
        )
    return normalized


def _idempotency_key(value: Any, *, required: bool) -> str:
    if required and value in (None, ""):
        raise VerificationValidationError(
            "Idempotency key is required.", code="VERIFICATION_INVALID_IDEMPOTENCY_KEY"
        )
    text = _text(
        value,
        field="idempotency_key",
        max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
        required=False,
    )
    if text and (len(text) < MIN_IDEMPOTENCY_KEY_LENGTH or not _IDEMPOTENCY_RE.fullmatch(text)):
        raise VerificationValidationError(
            "Invalid idempotency key.", code="VERIFICATION_INVALID_IDEMPOTENCY_KEY"
        )
    return text


def normalize_submit_payload(
    payload: dict[str, Any] | None,
    *,
    require_idempotency: bool = True,
) -> dict[str, Any]:
    raw = strip_transport_fields(payload)
    ensure_known_submit_fields(raw)
    verification_type = _verification_type(raw.get("verification_type"))
    result: dict[str, Any] = {
        "verification_type": verification_type,
        "verification_documents": _documents(raw.get("verification_documents")),
        "idempotency_key": _idempotency_key(raw.get("idempotency_key"), required=require_idempotency),
    }

    if verification_type == TYPE_INDIVIDUAL:
        result["legal_name"] = _text(
            raw.get("legal_name"),
            field="legal_name",
            max_length=MAX_LEGAL_NAME_LENGTH,
            required=True,
        )
        try:
            phone = validate_phone(raw.get("phone_number"))
        except Exception as exc:
            raise VerificationValidationError("Invalid phone number.") from exc
        if not phone:
            raise VerificationValidationError("Phone number is required.")
        result["phone_number"] = phone
        return result

    result["business_name"] = _text(
        raw.get("business_name"),
        field="business_name",
        max_length=MAX_BUSINESS_NAME_LENGTH,
        required=True,
    )
    business_type = _text(raw.get("business_type"), field="business_type", max_length=64, required=True)
    if business_type not in BUSINESS_TYPES:
        raise VerificationValidationError("Invalid business type.")
    result["business_type"] = business_type
    result["business_category"] = _text(
        raw.get("business_category"),
        field="business_category",
        max_length=MAX_BUSINESS_CATEGORY_LENGTH,
        required=True,
    )
    try:
        business_phone = validate_phone(raw.get("business_phone_number"))
    except Exception as exc:
        raise VerificationValidationError("Invalid business phone number.") from exc
    if not business_phone:
        raise VerificationValidationError("Business phone number is required.")
    result["business_phone_number"] = business_phone
    result["business_email"] = _email(raw.get("business_email"))
    result["business_website"] = _website(raw.get("business_website"))
    result["business_address"] = _text(
        raw.get("business_address"),
        field="business_address",
        max_length=MAX_BUSINESS_ADDRESS_LENGTH,
        required=True,
        multiline=True,
    )
    return result
