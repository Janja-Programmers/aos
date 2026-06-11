"""
Seller API validators and normalization helpers.
"""

from __future__ import annotations

from typing import Any

import frappe

from .constants import (
    LOCATION_INSTRUCTIONS_MAX_LENGTH,
    LOCATION_NAME_MAX_LENGTH,
)


def validate_optional_string(
    *,
    value: Any,
    label: str,
    max_length: int,
) -> str | None:
    """
    Normalize and validate optional text.

    Returns:
        Trimmed string when provided.
        None when the value is empty.
    """

    normalized = normalize_optional_string(
        value
    )

    if normalized is None:
        return None

    if len(normalized) > max_length:
        frappe.throw(
            f"{label} cannot exceed "
            f"{max_length} characters."
        )

    return normalized


def validate_location_name(
    value: Any,
) -> str | None:
    """Validate an optional seller location name."""

    return validate_optional_string(
        value=value,
        label="Location name",
        max_length=LOCATION_NAME_MAX_LENGTH,
    )


def validate_location_instructions(
    value: Any,
) -> str | None:
    """Validate optional seller location instructions."""

    return validate_optional_string(
        value=value,
        label="Location instructions",
        max_length=LOCATION_INSTRUCTIONS_MAX_LENGTH,
    )


def normalize_optional_string(
    value: Any,
) -> str | None:
    """Trim optional text and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(
        value
    ).strip()

    return normalized or None
