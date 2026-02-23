from __future__ import annotations

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.responses import fail
from aos.api.shared.validators import resolve_country
from aos.utils.aos_settings import get_aos_settings_snapshot


def resolve_market_country(country: str | None = None):
    """
    Resolve and enforce market country.

    Rules:
    - Logged-in users: ALWAYS use stored preference.
    - Guest:
        1) Use request country if provided.
        2) Otherwise fallback to AOS Settings default.
    """

    user = current_user()

    # Logged-in users → always use preference
    if user and user != "Guest":
        pref_country = frappe.db.get_value(
            "AOS User Preference",
            {"user": user},
            "country",
        )

        if not pref_country:
            return None, fail(
                "User preference not configured.",
                code="CONFIG_ERROR",
            )

        return pref_country, None

    # Guest → use request param if provided
    if country:
        country_name, error = resolve_country(country)
        if error:
            return None, error
        return country_name, None

    # Fallback → AOS Settings default
    settings = get_aos_settings_snapshot()

    if not settings.default_country:
        return None, fail(
            "Default country not configured.",
            code="CONFIG_ERROR",
        )

    # Validate default exists
    country_name, error = resolve_country(settings.default_country)
    if error:
        return None, fail(
            "System default country is invalid.",
            code="CONFIG_ERROR",
        )

    return country_name, None

def resolve_market_currency(currency: str | None = None):
    """
    Resolve and enforce market currency.

    Rules:
    - Logged-in users: ALWAYS use stored preference.
    - Guest:
        1) Use request currency if provided.
        2) Otherwise fallback to AOS Settings default.
    """

    user = current_user()

    # Logged-in users → always use preference
    if user and user != "Guest":
        pref_currency = frappe.db.get_value(
            "AOS User Preference",
            {"user": user},
            "currency",
        )

        if not pref_currency:
            return None, fail(
                "User currency preference not configured.",
                code="CONFIG_ERROR",
            )

        return pref_currency, None

    # Guest → use request param if provided
    if currency:
        currency = currency.strip()

        if not frappe.db.exists("Currency", currency):
            return None, fail(
                "Invalid currency.",
                code="VALIDATION_ERROR",
            )

        return currency, None

    # Fallback → AOS Settings default
    settings = get_aos_settings_snapshot()

    if not settings.default_currency:
        return None, fail(
            "Default currency not configured.",
            code="CONFIG_ERROR",
        )

    if not frappe.db.exists("Currency", settings.default_currency):
        return None, fail(
            "System default currency is invalid.",
            code="CONFIG_ERROR",
        )

    return settings.default_currency, None
