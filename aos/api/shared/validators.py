from __future__ import annotations

import frappe

from aos.api.shared.responses import fail


def resolve_country(value: str | None):
    """Resolve a country input to Country.name.

    Accepts either:
        - Country.name (e.g. "Kenya")
        - Country.code (e.g. "KE")
    """

    value = (value or "").strip()
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
        code="VALIDATION_ERROR",
        data={"field": "country"},
    )


def resolve_language(value: str | None):
    """Resolve a language input to Language.name.

    Accepts either:
        - Language.name (e.g. "en")
        - Language.language_name (e.g. "English")
    """

    value = (value or "").strip()
    if not value:
        return None, None

    # Direct match (Language.name = language_code)
    if frappe.db.exists("Language", value):
        return value, None

    # Match by language_name
    language_name = frappe.db.get_value(
        "Language",
        {"language_name": value},
        "name",
    )
    if language_name:
        return language_name, None

    return None, fail(
        "Invalid language.",
        code="VALIDATION_ERROR",
        data={"field": "language"},
    )


def resolve_currency(value: str | None):
    """Resolve a currency input to Currency.name (currency code).

    Accepts:
        - Currency.name (e.g. "KES")
        - Currency.symbol (e.g. "KSh")
    """

    value = (value or "").strip()
    if not value:
        return None, None

    # Direct match (Currency.name)
    if frappe.db.exists("Currency", value):
        return value, None

    # Match by symbol
    currency_name = frappe.db.get_value(
        "Currency",
        {"symbol": value},
        "name",
    )
    if currency_name:
        return currency_name, None

    return None, fail(
        "Invalid currency.",
        code="VALIDATION_ERROR",
        data={"field": "currency"},
    )


def resolve_location(location: str | None, *, country: str | None = None):
    """Resolve a location input to AOS Location.name.

    Args:
        location: AOS Location.name.
        country: Optional Country.name or ISO code.
                 If provided, enforces that the location belongs to that country.
    """

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
            code="VALIDATION_ERROR",
            data={"field": "location"},
        )

    if not location_doc.is_active:
        return None, fail(
            "Selected location is inactive.",
            code="VALIDATION_ERROR",
            data={"field": "location"},
        )

    if country:
        country_name, error = resolve_country(country)
        if error:
            return None, error

        if location_doc.country != country_name:
            return None, fail(
                "Location does not belong to the selected country.",
                code="VALIDATION_ERROR",
                data={"field": "location"},
            )

    return location, None
