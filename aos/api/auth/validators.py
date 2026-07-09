"""Auth-specific validation helpers.

These helpers intentionally reject structured values instead of coercing them to
strings. Auth endpoints must never accept dict/list/int/bool input for strings,
passwords, OTPs, or tokens.
"""

from __future__ import annotations

import re
from typing import Any

from aos.api.shared.responses import fail


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IDENTIFIER_MAX_LEN = 254
EMAIL_MAX_LEN = 254
NAME_MAX_LEN = 140
PASSWORD_MAX_LEN = 1024
OTP_MAX_LEN = 12
TOKEN_MAX_LEN = 4096
REASON_MAX_LEN = 300
COUNTRY_MAX_LEN = 140
LANGUAGE_MAX_LEN = 140
CURRENCY_MAX_LEN = 32
CLIENT_TYPES = {"mobile", "web"}


def require_string(
    value: Any,
    field: str,
    *,
    max_length: int | None = None,
    strip: bool = True,
):
    """Validate and return a required string field.

    Returns ``(value, None)`` on success or ``(None, fail(...))`` on failure.
    Booleans, numbers, lists, dicts, and null are rejected instead of coerced.
    """

    if not isinstance(value, str):
        return None, fail(
            f"{field} must be a string.",
            error="VALIDATION_ERROR",
            data={"field": field},
        )

    cleaned = value.strip() if strip else value
    if not cleaned:
        return None, fail(
            f"{field} is required.",
            error="VALIDATION_ERROR",
            data={"field": field},
        )

    if max_length is not None and len(cleaned) > max_length:
        return None, fail(
            f"{field} is too long.",
            error="VALIDATION_ERROR",
            data={"field": field, "max_length": max_length},
        )

    return cleaned, None


def optional_string(
    value: Any,
    field: str,
    *,
    max_length: int | None = None,
    strip: bool = True,
):
    """Validate and return an optional string field.

    ``None`` and blank strings are returned as ``None``. Structured/non-string
    values are rejected.
    """

    if value is None:
        return None, None

    if not isinstance(value, str):
        return None, fail(
            f"{field} must be a string.",
            error="VALIDATION_ERROR",
            data={"field": field},
        )

    cleaned = value.strip() if strip else value
    if not cleaned:
        return None, None

    if max_length is not None and len(cleaned) > max_length:
        return None, fail(
            f"{field} is too long.",
            error="VALIDATION_ERROR",
            data={"field": field, "max_length": max_length},
        )

    return cleaned, None


def require_email(value: Any, field: str = "email"):
    email, err = require_string(value, field, max_length=EMAIL_MAX_LEN)
    if err:
        return None, err

    email = email.lower()
    if not EMAIL_REGEX.match(email):
        return None, fail(
            "A valid email is required.",
            error="VALIDATION_ERROR",
            data={"field": field},
        )

    return email, None


def require_identifier(value: Any, field: str = "identifier"):
    identifier, err = require_string(value, field, max_length=IDENTIFIER_MAX_LEN)
    if err:
        return None, err
    return identifier.lower(), None


def require_password(value: Any, field: str = "password"):
    return require_string(value, field, max_length=PASSWORD_MAX_LEN, strip=False)


def require_otp(value: Any, field: str = "otp"):
    otp, err = require_string(value, field, max_length=OTP_MAX_LEN)
    if err:
        return None, err
    return otp, None


def require_token(value: Any, field: str):
    return require_string(value, field, max_length=TOKEN_MAX_LEN)


def normalize_email(email: str) -> str:
    """Normalize a trusted string email value."""
    return email.strip().lower()[:EMAIL_MAX_LEN]


def normalize_identifier(identifier: str) -> str:
    """Normalize a trusted string login identifier."""
    return identifier.strip().lower()[:IDENTIFIER_MAX_LEN]


def normalize_name(full_name: str) -> str:
    value = re.sub(r"\s+", " ", full_name.strip())
    return value[:NAME_MAX_LEN]


def normalize_otp(value: str) -> str:
    return value.strip()[:OTP_MAX_LEN]


def validate_registration_inputs(email: Any, password: Any, full_name: Any):
    email_value, email_err = require_email(email)
    if email_err:
        return None, email_err

    name_value, name_err = require_string(full_name, "full_name", max_length=NAME_MAX_LEN)
    if name_err:
        return None, name_err
    name_value = normalize_name(name_value)
    if len(name_value) < 2:
        return None, fail("Full name is required.", error="VALIDATION_ERROR", data={"field": "full_name"})

    password_value, password_err = require_password(password)
    if password_err:
        return None, password_err

    pw_err = validate_password_strength(password_value)
    if pw_err:
        return None, pw_err

    return {
        "email": email_value,
        "password": password_value,
        "full_name": name_value,
    }, None


def validate_email(email: str):
    # Internal helper for trusted normalized strings.
    if not isinstance(email, str) or not email or not EMAIL_REGEX.match(email):
        return fail("A valid email is required.", error="VALIDATION_ERROR", data={"field": "email"})
    return None


def validate_login_inputs(identifier: str, password: str):
    # Internal helper for already type-checked values.
    if not identifier:
        return fail("Email or username is required.", error="VALIDATION_ERROR", data={"field": "identifier"})
    if len(identifier) > IDENTIFIER_MAX_LEN:
        return fail("Email or username is too long.", error="VALIDATION_ERROR", data={"field": "identifier"})
    if not isinstance(password, str) or not password:
        return fail("Password is required.", error="VALIDATION_ERROR", data={"field": "password"})
    if len(password) > PASSWORD_MAX_LEN:
        return fail("Password is too long.", error="VALIDATION_ERROR", data={"field": "password"})
    return None


def validate_password_strength(password: str):
    if not isinstance(password, str) or not password:
        return fail("Password is required.", error="VALIDATION_ERROR", data={"field": "password"})
    if len(password) < 8:
        return fail("Password must be at least 8 characters long.", error="VALIDATION_ERROR", data={"field": "password"})
    if len(password) > PASSWORD_MAX_LEN:
        return fail("Password is too long.", error="VALIDATION_ERROR", data={"field": "password"})
    return None


def validate_client_type(value: Any):
    client_type, err = require_string(value, "client_type", max_length=16)
    if err:
        return None, err

    client_type = client_type.lower()
    if client_type not in CLIENT_TYPES:
        return None, fail(
            "Client type must be either mobile or web.",
            error="VALIDATION_ERROR",
            data={"field": "client_type"},
        )

    return client_type, None


def optional_bootstrap_inputs(kwargs: dict[str, Any]):
    """Validate optional country/currency/language auth bootstrap inputs."""

    country, err = optional_string(kwargs.get("country"), "country", max_length=COUNTRY_MAX_LEN)
    if err:
        return None, err
    currency, err = optional_string(kwargs.get("currency"), "currency", max_length=CURRENCY_MAX_LEN)
    if err:
        return None, err
    language, err = optional_string(kwargs.get("language"), "language", max_length=LANGUAGE_MAX_LEN)
    if err:
        return None, err

    return {"country": country, "currency": currency, "language": language}, None
