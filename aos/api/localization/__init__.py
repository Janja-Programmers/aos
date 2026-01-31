"""Localization endpoints.

This feature module exposes locale/config and user preference APIs used by mobile clients.

Pattern (consistent with aos.api.auth and aos.api.accounts):
  - whitelisted wrappers live in this __init__.py
  - business logic lives in sibling modules
"""

from __future__ import annotations

import frappe

from .bundle import get_locale_bundle_impl
from .locations import get_locations_impl
from .preferences import get_my_preferences_impl, update_preferences_impl


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_locale_bundle():
    """Return locale bundle used at app startup.

    Includes global locale config (base currency/defaults) and selector lists
    (countries, languages, currencies).
    """

    return get_locale_bundle_impl()


@frappe.whitelist(methods=["GET"])
def get_my_preferences():
    """Return current user's saved localization preferences (or null)."""

    return get_my_preferences_impl()


@frappe.whitelist(methods=["POST"])
def update_preferences(
    country: str | None = None,
    language: str | None = None,
    currency: str | None = None,
    timezone: str | None = None,
    override_language: int | None = None,
    override_currency: int | None = None,
):
    """Update current user's localization preferences."""

    return update_preferences_impl(
        country=country,
        language=language,
        currency=currency,
        timezone=timezone,
        override_language=bool(int(override_language or 0)) if override_language is not None else None,
        override_currency=bool(int(override_currency or 0)) if override_currency is not None else None,
    )


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_locations(country: str | None = None, include_inactive: int | None = None):
    """Return AOS locations for a selected country.

    Args:
        country: Country.name or Country.code.
        include_inactive: optional flag (0/1). Defaults to 0.
    """

    return get_locations_impl(
        country=country,
        include_inactive=bool(int(include_inactive or 0)),
    )
