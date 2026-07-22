"""Backward-compatible endpoint validators backed by Accounts domain validation."""

from __future__ import annotations

from aos.api.shared.responses import fail
from aos.services.accounts.errors import AccountValidationError
from aos.services.accounts.validation import validate_bio as _validate_bio
from aos.services.accounts.validation import validate_display_name


def validate_full_name(value):
    try:
        return validate_display_name(value), None
    except AccountValidationError as exc:
        return None, fail(str(exc), error=exc.code, http_status=exc.http_status)


def validate_bio(value):
    try:
        return _validate_bio(value), None
    except AccountValidationError as exc:
        return None, fail(str(exc), error=exc.code, http_status=exc.http_status)
