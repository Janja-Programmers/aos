"""Central Accounts profile validation and mass-assignment policy."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Any, Callable

from .errors import AccountValidationError

DISPLAY_NAME_MIN_LEN = 2
DISPLAY_NAME_MAX_LEN = 80
LEGAL_NAME_MIN_LEN = 2
LEGAL_NAME_MAX_LEN = 160
BIO_MAX_LEN = 500
PHONE_MAX_LEN = 32
LOCATION_MAX_LEN = 160
AVATAR_MEDIA_ID_RE = re.compile(r"^MEDIA-[A-Z0-9-]{6,64}$", re.IGNORECASE)
PHONE_RE = re.compile(r"^\+[1-9][0-9]{6,14}$")
ALLOWED_GENDERS = {"", "Male", "Female", "Other", "Prefer not to say"}

PROFILE_ALIASES = {
    "full_name": "display_name",
    "mobile_no": "phone",
    "birth_date": "date_of_birth",
    "user_image_media": "avatar_media_id",
    "profile_image_media": "avatar_media_id",
    "media_id": "avatar_media_id",
}
EDITABLE_PROFILE_FIELDS = {
    "display_name",
    "legal_name",
    "phone",
    "date_of_birth",
    "gender",
    "bio",
    "location",
    "avatar_media_id",
    "remove_avatar",
}
SYSTEM_MANAGED_PROFILE_FIELDS = {
    "user",
    "email",
    "roles",
    "enabled",
    "account_status",
    "is_deleted",
    "is_verified",
    "verified_by",
    "verified_on",
    "seller",
    "seller_type",
    "verification_status",
    "public_id",
}


def _normalized_text(value: Any, *, field: str, max_length: int, required: bool = False) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    if "\x00" in text or any(unicodedata.category(ch) == "Cc" and ch not in "\n\t" for ch in text):
        raise AccountValidationError(f"Invalid {field}.", code=f"INVALID_{field.upper()}")
    text = re.sub(r"[ \t]+", " ", text.strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    if required and not text:
        raise AccountValidationError(f"{field.replace('_', ' ').title()} is required.", code=f"INVALID_{field.upper()}")
    if len(text) > max_length:
        raise AccountValidationError(f"{field.replace('_', ' ').title()} is too long.", code=f"INVALID_{field.upper()}")
    return text


def validate_display_name(value: Any) -> str:
    text = _normalized_text(value, field="display_name", max_length=DISPLAY_NAME_MAX_LEN, required=True)
    if len(text) < DISPLAY_NAME_MIN_LEN:
        raise AccountValidationError("Display name is too short.", code="INVALID_DISPLAY_NAME")
    return text


def validate_legal_name(value: Any) -> str:
    text = _normalized_text(value, field="legal_name", max_length=LEGAL_NAME_MAX_LEN)
    if text and len(text) < LEGAL_NAME_MIN_LEN:
        raise AccountValidationError("Legal name is too short.", code="INVALID_LEGAL_NAME")
    return text


def validate_bio(value: Any) -> str:
    return _normalized_text(value, field="bio", max_length=BIO_MAX_LEN)


def validate_location(value: Any) -> str:
    return _normalized_text(value, field="location", max_length=LOCATION_MAX_LEN)


def validate_phone(value: Any) -> str:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not raw:
        return ""
    if "\x00" in raw:
        raise AccountValidationError("Invalid phone number.", code="INVALID_PHONE_NUMBER")
    normalized = re.sub(r"[\s().-]+", "", raw)
    if normalized.startswith("00"):
        normalized = "+" + normalized[2:]
    if len(normalized) > PHONE_MAX_LEN or not PHONE_RE.fullmatch(normalized):
        raise AccountValidationError("Invalid phone number.", code="INVALID_PHONE_NUMBER")
    return normalized


def validate_date_of_birth(value: Any) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        parsed = value.date()
    elif isinstance(value, dt.date):
        parsed = value
    else:
        try:
            parsed = dt.date.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise AccountValidationError("Invalid date of birth.", code="INVALID_DATE_OF_BIRTH") from exc
    today = dt.date.today()
    if parsed > today or parsed < dt.date(1900, 1, 1):
        raise AccountValidationError("Invalid date of birth.", code="INVALID_DATE_OF_BIRTH")
    return parsed


def validate_gender(value: Any) -> str:
    text = _normalized_text(value, field="gender", max_length=32)
    if text not in ALLOWED_GENDERS:
        raise AccountValidationError("Invalid gender.", code="INVALID_GENDER")
    return text


def validate_avatar_media_id(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw or not AVATAR_MEDIA_ID_RE.fullmatch(raw):
        raise AccountValidationError("Invalid avatar media.", code="INVALID_AVATAR_MEDIA")
    return raw


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


_VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "display_name": validate_display_name,
    "legal_name": validate_legal_name,
    "phone": validate_phone,
    "date_of_birth": validate_date_of_birth,
    "gender": validate_gender,
    "bio": validate_bio,
    "location": validate_location,
    "avatar_media_id": validate_avatar_media_id,
    "remove_avatar": _truthy,
}


def validate_profile_patch(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    normalized: dict[str, Any] = {}
    unknown: list[str] = []
    for key, value in raw.items():
        canonical = PROFILE_ALIASES.get(str(key), str(key))
        if canonical in SYSTEM_MANAGED_PROFILE_FIELDS or canonical not in EDITABLE_PROFILE_FIELDS:
            unknown.append(str(key))
            continue
        if canonical in normalized and normalized[canonical] != value:
            raise AccountValidationError("Conflicting profile fields.", code="INVALID_PROFILE_FIELD")
        normalized[canonical] = value
    if unknown:
        raise AccountValidationError(
            f"Unsupported profile fields: {', '.join(sorted(unknown))}.",
            code="INVALID_PROFILE_FIELD",
        )
    if not normalized:
        raise AccountValidationError("At least one profile field is required.", code="INVALID_PROFILE_FIELD")
    if normalized.get("remove_avatar") and "avatar_media_id" in normalized:
        raise AccountValidationError("Avatar replacement and removal cannot be combined.", code="INVALID_AVATAR_MEDIA")
    return {field: _VALIDATORS[field](value) for field, value in normalized.items()}
