"""Validators for profile updates."""

from __future__ import annotations

import re
import frappe

from aos.api.shared.responses import fail

from .constants import (
    FULL_NAME_MIN_LEN,
    FULL_NAME_MAX_LEN,
    BIO_MAX_LEN,
)


def validate_full_name(value: str):
    value = (value or "").strip()

    if not value:
        return None, fail("Full name is required.", code="VALIDATION_ERROR")

    if len(value) < FULL_NAME_MIN_LEN:
        return None, fail("Full name is too short.", code="VALIDATION_ERROR")

    if len(value) > FULL_NAME_MAX_LEN:
        return None, fail("Full name is too long.", code="VALIDATION_ERROR")

    value = re.sub(r"\s+", " ", value)

    return value, None


def validate_bio(value: str):
    value = (value or "").strip()

    # Normalize excessive spaces/tabs while preserving normal line breaks.
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)

    if len(value) > BIO_MAX_LEN:
        return None, fail(
            f"Bio is too long. Maximum is {BIO_MAX_LEN} characters.",
            code="VALIDATION_ERROR",
        )

    return value, None
