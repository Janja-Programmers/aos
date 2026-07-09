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

    value, err = _clean_optional_string(value, "country")
    if err:
        return None, err
    if not value:
        return None, None

    # Direct match (Country.name)
    if frappe.db.exists("Country", value):
        return value, None

    # Match by ISO code
    country_name = frappe.db.get_value("Country", {"code": value}, "name")
    if country_name:
        return country_name, None

    return None, fail(
        "Invalid country.",
        error="VALIDATION_ERROR",
        data={"field": "country"},
    )


def resolve_language(value: str | None):
    """Resolve a language input to Language.name."""

    value, err = _clean_optional_string(value, "language")
    if err:
        return None, err
    if not value:
        return None, None

    if frappe.db.exists("Language", value):
        return value, None

    language_name = frappe.db.get_value(
        "Language",
        {"language_name": value},
        "name",
    )
    if language_name:
        return language_name, None

    return None, fail(
        "Invalid language.",
        error="VALIDATION_ERROR",
        data={"field": "language"},
    )


def resolve_currency(value: str | None):
    """Resolve a currency input to Currency.name."""

    value, err = _clean_optional_string(value, "currency")
    if err:
        return None, err
    if not value:
        return None, None

    if frappe.db.exists("Currency", value):
        return value, None

    currency_name = frappe.db.get_value(
        "Currency",
        {"symbol": value},
        "name",
    )
    if currency_name:
        return currency_name, None

    return None, fail(
        "Invalid currency.",
        error="VALIDATION_ERROR",
        data={"field": "currency"},
    )


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
            error="VALIDATION_ERROR",
            data={"field": "location"},
        )

    if not location_doc.is_active:
        return None, fail(
            "Selected location is inactive.",
            error="VALIDATION_ERROR",
            data={"field": "location"},
        )

    if country:
        country_name, error = resolve_country(country)
        if error:
            return None, error

        if location_doc.country != country_name:
            return None, fail(
                "Location does not belong to the selected country.",
                error="VALIDATION_ERROR",
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
