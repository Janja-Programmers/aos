from __future__ import annotations

import frappe

from aos.api.shared.responses import fail


def resolve_country(value: str | None):
    """Resolve a country input to Country.name.

    Accepts either Country.name (e.g. "Kenya") or Country.code (e.g. "KE").
    """
    v = (value or "").strip()
    if not v:
        return None, None

    if frappe.db.exists("Country", v):
        return v, None

    name = frappe.db.get_value("Country", {"code": v.upper()}, "name")
    if name:
        return name, None

    return None, fail("Invalid country.", code="VALIDATION_ERROR", data={"field": "country"})


def resolve_language(value: str | None):
    """Resolve a language input to Language.name.

    Accepts either Language.name (e.g. "en") or Language.language_name (e.g. "English").
    """
    v = (value or "").strip()
    if not v:
        return None, None

    if frappe.db.exists("Language", v):
        return v, None

    name = frappe.db.get_value("Language", {"language_name": v}, "name")
    if name:
        return name, None

    return None, fail("Invalid language.", code="VALIDATION_ERROR", data={"field": "language"})


def resolve_currency(value: str | None):
    """Resolve a currency input to Currency.name (currency code)."""
    v = (value or "").strip()
    if not v:
        return None, None

    code = v.upper()
    if frappe.db.exists("Currency", code):
        return code, None

    return None, fail("Invalid currency.", code="VALIDATION_ERROR", data={"field": "currency"})


def resolve_location(location: str | None, *, country: str | None = None):
    """Resolve a location input to AOS Location.name.

    Args:
        location: AOS Location.name.
        country: optional Country.name / code. If provided, enforces match.
    """

    v = (location or "").strip()
    if not v:
        return None, None

    if not frappe.db.exists("AOS Location", v):
        return None, fail("Invalid location.", code="VALIDATION_ERROR", data={"field": "location"})

    if country:
        country_name, err = resolve_country(country)
        if err:
            return None, err
        loc_country = frappe.db.get_value("AOS Location", v, "country")
        if country_name and loc_country and loc_country != country_name:
            return None, fail(
                "Location does not belong to the selected country.",
                code="VALIDATION_ERROR",
                data={"field": "location"},
            )

    return v, None
