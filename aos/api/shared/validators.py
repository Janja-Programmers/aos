from __future__ import annotations

import frappe

from aos.api.shared.responses import fail
from aos.api.shared.auth import current_user


# COUNTRY / LANGUAGE / CURRENCY
def _clean_optional_string(value, field: str):
    if value is None:
        return "", None
    if not isinstance(value, str):
        return "", fail(
            f"{field} must be a string.",
            error="VALIDATION_ERROR",
            data={"field": field},
        )
    return value.strip(), None


def resolve_country(value: str | None):
    """Resolve a country input to Country.name.

    Accepts either:
        - Country.name (e.g. "Kenya")
        - Country.code (e.g. "KE")
    """

    from aos.services.localization import validate_country
    return validate_country(value, required=False)


def resolve_language(value: str | None):
    """Resolve a language input to Language.name."""

    from aos.services.localization import validate_language
    return validate_language(value, required=False)


def resolve_currency(value: str | None):
    """Resolve a currency input to Currency.name."""

    from aos.services.localization import validate_currency
    return validate_currency(value, required=False)


# LOCATION
def resolve_location(location: str | None, *, country: str | None = None):
    """Resolve a location input to AOS Location.name."""

    location = (location or "").strip()
    if not location:
        return None, None

    location_doc = frappe.db.get_value(
        "AOS Location",
        location,
        ["country", "is_active"],
        as_dict=True,
    )

    if not location_doc:
        return None, fail(
            "Invalid location.",
            error="INVALID_LOCATION",
            data={"field": "location"},
        )

    if not location_doc.is_active:
        return None, fail(
            "Selected location is inactive.",
            error="INVALID_LOCATION",
            data={"field": "location"},
        )

    if country:
        country_name, error = resolve_country(country)
        if error:
            return None, error

        if location_doc.country != country_name:
            return None, fail(
                "Location does not belong to the selected country.",
                error="INVALID_LOCATION",
                data={"field": "location"},
            )

    return location, None


# GENERIC VALIDATORS
def require_id(value: str | None, field: str):
    """Ensure a required ID field is provided."""
    value = (value or "").strip()

    if not value:
        return None, fail(
            f"{field} is required",
            error="VALIDATION_ERROR",
            data={"field": field},
        )

    return value, None


# SESSION VALIDATION
def require_session_for_guest(session_id: str | None):
    """
    Enforce session_id for guest users.
    Logged-in users do not require session_id.
    """
    user = current_user()

    if user == "Guest" and not session_id:
        return None, fail(
            "Session ID required for guest users",
            error="VALIDATION_ERROR",
            data={"field": "session_id"},
        )

    return session_id, None


# TRACKING / ANALYTICS
def normalize_watch_ms(watch_ms):
    """
    Normalize watch time (ms):
    - Converts to int
    - Prevents negative values
    """
    try:
        watch_ms = int(watch_ms)
    except Exception:
        return None, fail(
            "Invalid watch time",
            error="VALIDATION_ERROR",
            data={"field": "watch_ms"},
        )

    return max(watch_ms, 0), None


# OPTIONAL HELPER
def unwrap(result):
    """
    Utility to unwrap (value, error) pattern.
    """
    value, error = result
    if error:
        return None, error
    return value, None
