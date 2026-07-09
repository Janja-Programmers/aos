from __future__ import annotations

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.responses import fail
from aos.api.shared.validators import resolve_country
from aos.utils.aos_settings import get_aos_settings_snapshot


# INTERNAL HELPERS
def _get_user_preference(user: str):
    """
    Fetch user preference with light caching.
    """
    cache = frappe.cache()
    cache_key = f"aos:user_pref:{user}"

    cached = cache.get_value(cache_key)
    if cached:
        return cached

    pref = frappe.db.get_value(
        "AOS User Preference",
        {"user": user},
        ["country", "currency"],
        as_dict=True,
    )

    if pref:
        cache.set_value(cache_key, pref, expires_in_sec=300)  # 5 min cache

    return pref


# COUNTRY
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
        pref = _get_user_preference(user)

        if not pref or not pref.get("country"):
            return None, fail(
                "User preference not configured.",
                error="CONFIG_ERROR",
            )

        return pref["country"], None

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
            error="CONFIG_ERROR",
        )

    # Validate default exists
    country_name, error = resolve_country(settings.default_country)
    if error:
        return None, fail(
            "System default country is invalid.",
            error="CONFIG_ERROR",
        )

    return country_name, None

# CURRENCY
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
        pref = _get_user_preference(user)

        if not pref or not pref.get("currency"):
            return None, fail(
                "User currency preference not configured.",
                error="CONFIG_ERROR",
            )

        return pref["currency"], None

    # Guest → use request param if provided
    if currency:
        currency = currency.strip()

        if not frappe.db.exists("Currency", currency):
            return None, fail(
                "Invalid currency.",
                error="VALIDATION_ERROR",
            )

        return currency, None

    # Fallback → AOS Settings default
    settings = get_aos_settings_snapshot()

    if not settings.default_currency:
        return None, fail(
            "Default currency not configured.",
            error="CONFIG_ERROR",
        )

    if not frappe.db.exists("Currency", settings.default_currency):
        return None, fail(
            "System default currency is invalid.",
            error="CONFIG_ERROR",
        )

    return settings.default_currency, None

# COMBINED
def resolve_market_context(
    country: str | None = None,
    currency: str | None = None,
):
    """
    Resolve both country and currency in one call.

    Returns:
        (country, currency, error)
    """

    country, error = resolve_market_country(country)
    if error:
        return None, None, error

    currency, error = resolve_market_currency(currency)
    if error:
        return None, None, error

    return country, currency, None
