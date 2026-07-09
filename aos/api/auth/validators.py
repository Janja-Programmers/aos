"""Auth-specific validation helpers."""

from __future__ import annotations

import re
from typing import Any

from aos.api.shared.responses import fail


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IDENTIFIER_MAX_LEN = 254
NAME_MAX_LEN = 140
PASSWORD_MAX_LEN = 1024
OTP_MAX_LEN = 12


def _as_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    return "" if value is None else str(value)


def normalize_email(email: str) -> str:
    return _as_string(email).strip().lower()[:IDENTIFIER_MAX_LEN]


def normalize_identifier(identifier: str) -> str:
    """Normalize login identifiers without accepting structured input."""

    return _as_string(identifier).strip().lower()[:IDENTIFIER_MAX_LEN]


def normalize_name(full_name: str) -> str:
    value = re.sub(r"\s+", " ", _as_string(full_name).strip())
    return value[:NAME_MAX_LEN]


def normalize_otp(value: str) -> str:
    return _as_string(value).strip()[:OTP_MAX_LEN]


def validate_registration_inputs(email: str, password: str, full_name: str):
    if not full_name or len(full_name.strip()) < 2:
        return fail("Full name is required.", error="VALIDATION_ERROR", data={"field": "full_name"})
    if len(full_name) > NAME_MAX_LEN:
        return fail("Full name is too long.", error="VALIDATION_ERROR", data={"field": "full_name"})
    if not email or not EMAIL_REGEX.match(email.strip().lower()):
        return fail("A valid email is required.", error="VALIDATION_ERROR", data={"field": "email"})
    if not password or not isinstance(password, str):
        return fail("Password is required.", error="VALIDATION_ERROR", data={"field": "password"})
    if len(password) < 8:
        return fail("Password must be at least 8 characters long.", error="VALIDATION_ERROR", data={"field": "password"})
    if len(password) > PASSWORD_MAX_LEN:
        return fail("Password is too long.", error="VALIDATION_ERROR", data={"field": "password"})
    return None


def validate_email(email: str):
    if not email or not EMAIL_REGEX.match(email):
        return fail("A valid email is required.", error="VALIDATION_ERROR", data={"field": "email"})
    return None


def validate_login_inputs(identifier: str, password: str):
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
    if not isinstance(value, str) or not value.strip():
        return None, fail(
            "Client type is required.",
            error="VALIDATION_ERROR",
            data={"field": "client_type"},
        )

    client_type = value.strip().lower()
    if client_type not in {"mobile", "web"}:
        return None, fail(
            "Client type must be either mobile or web.",
            error="VALIDATION_ERROR",
            data={"field": "client_type"},
        )

    return client_type, None
