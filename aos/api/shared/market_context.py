from __future__ import annotations

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.responses import fail
from aos.services.localization_service import get_default_preferences, validate_country, validate_currency


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
        ["country", "currency", "language"],
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
        country_name, error = validate_country(country)
        if error:
            return None, error
        return country_name, None

    # Fallback → AOS Settings default
    defaults, error = get_default_preferences()
    return (defaults["country"], None) if defaults else (None, error)

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
        return validate_currency(currency)

    # Fallback → AOS Settings default
    defaults, error = get_default_preferences()
    return (defaults["currency"], None) if defaults else (None, error)

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
