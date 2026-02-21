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

    # Logged-in users
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

    # Guest users
    if country:
        country_name, error = resolve_country(country)
        if error:
            return None, error
        return country_name, None

    # Fallback to AOS Settings
    settings = get_aos_settings_snapshot()

    if not settings.default_country:
        return None, fail(
            "Default country not configured.",
            code="CONFIG_ERROR",
        )

    # Validate default_country exists
    country_name, error = resolve_country(settings.default_country)
    if error:
        return None, fail(
            "System default country is invalid.",
            code="CONFIG_ERROR",
        )

    return country_name, None