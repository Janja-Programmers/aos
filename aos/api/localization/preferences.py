from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok

from .constants import PREFERENCES_UPDATE_LIMIT_PER_MIN_PER_USER
from .validators import resolve_country, resolve_language, resolve_currency


def _serialize_preferences(doc) -> dict:
    # Provide both names and codes where available.
    country_code = None
    if doc.country:
        country_code = frappe.db.get_value("Country", doc.country, "code")
        if country_code:
            country_code = country_code.upper()

    language_name = None
    if doc.language:
        language_name = frappe.db.get_value("Language", doc.language, "language_name")

    return {
        "user": doc.user,
        "country": {
            "name": doc.country or None,
            "code": country_code,
        },
        "language": {
            "name": language_name,
            "code": doc.language or None,
        },
        "currency": {
            "code": doc.currency or None,
        },
        "overrides": {
            "language": bool(int(doc.language_overridden or 0)),
            "currency": bool(int(doc.currency_overridden or 0)),
        },
    }


def get_my_preferences_impl():
    user, err = require_login()
    if err:
        return err

    if not frappe.db.exists("AOS User Preferences", user):
        return ok("No preferences saved.", None)

    doc = frappe.get_doc("AOS User Preferences", user)
    return ok("Preferences loaded.", _serialize_preferences(doc))


def update_preferences_impl(
    *,
    country: str | None = None,
    language: str | None = None,
    currency: str | None = None,
    timezone: str | None = None,  # reserved for later
    override_language: bool | None = None,
    override_currency: bool | None = None,
):
    user, err = require_login()
    if err:
        return err

    # Rate limit per user
    rl = rate_limit(
        key=f"aos:locale:prefs:update:user:{user}",
        ttl_seconds=60,
        limit=PREFERENCES_UPDATE_LIMIT_PER_MIN_PER_USER,
        message="Too many preference updates. Please try again later.",
    )
    if rl:
        return rl

    country_name, c_err = resolve_country(country)
    if c_err:
        return c_err
    language_name, l_err = resolve_language(language)
    if l_err:
        return l_err
    currency_code, cur_err = resolve_currency(currency)
    if cur_err:
        return cur_err

    # Load or create
    if frappe.db.exists("AOS User Preferences", user):
        doc = frappe.get_doc("AOS User Preferences", user)
    else:
        doc = frappe.get_doc({"doctype": "AOS User Preferences", "user": user})

    if country is not None:
        doc.country = country_name
    if language is not None:
        doc.language = language_name
    if currency is not None:
        doc.currency = currency_code

    if override_language is not None:
        doc.language_overridden = 1 if override_language else 0
    if override_currency is not None:
        doc.currency_overridden = 1 if override_currency else 0

    # Allow website users to update their own preferences.
    doc.save(ignore_permissions=True)

    return ok("Preferences updated.", _serialize_preferences(doc))
