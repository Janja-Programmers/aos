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

@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_locale_bundle():
    """Return locale bundle used at app startup.

    Includes global locale config (base currency/defaults) and selector lists
    (countries, languages, currencies).
    """

    return get_locale_bundle_impl()


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_locations(**kwargs):
    """List active locations for a country."""
    return get_locations_impl(country=kwargs.get("country"))
