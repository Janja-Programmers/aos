"""Account bootstrap helpers used by auth/session flows."""

from __future__ import annotations

from typing import Any
import hashlib

import frappe

from aos.api.shared.responses import fail
from aos.api.shared.validators import resolve_country, resolve_currency, resolve_language
from aos.utils.aos_settings import get_aos_settings_snapshot


DEFAULT_LANGUAGE_CODE = "en"
DEFAULT_CURRENCY_CODE = "USD"
DEFAULT_COUNTRY_CANDIDATES = ("Kenya", "United States")
DEFAULT_LANGUAGE_CANDIDATES = ("en", "English")
DEFAULT_CURRENCY_CANDIDATES = ("USD", "KES")


def _clear_preference_cache(user: str) -> None:
    try:
        frappe.cache().delete_value(f"aos:user_pref:{user}")
    except Exception:
        pass


def ensure_aos_profile(user: str):
    """Idempotently create the app profile required by auth/account status.

    ``ignore_permissions=True`` is intentional here: the authenticated subject is
    the same user being bootstrapped, and System Manager-only DocType permissions
    would otherwise prevent safe repair of required AOS identity rows during login/me.
    """

    if not user:
        return None

    if frappe.db.exists("AOS Profile", user):
        return frappe.get_doc("AOS Profile", user)

    profile = frappe.new_doc("AOS Profile")
    profile.user = user
    profile.account_status = "Active"
    profile.is_deleted = 0
    profile.insert(ignore_permissions=True)
    return profile


def _first_existing_doc(doctype: str, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if frappe.db.exists(doctype, candidate):
            return candidate
    return frappe.db.get_value(doctype, {}, "name")


def _resolve_default_country(country: str | None):
    if country:
        return resolve_country(country)

    settings = get_aos_settings_snapshot()
    configured = settings.default_country or _first_existing_doc("Country", DEFAULT_COUNTRY_CANDIDATES)
    if not configured:
        return None, fail("Default country not configured.", error="CONFIG_ERROR")

    country_name, error = resolve_country(configured)
    if error:
        return None, fail("System default country is invalid.", error="CONFIG_ERROR")
    return country_name, None


def _resolve_default_currency(currency: str | None):
    if currency:
        return resolve_currency(currency)

    settings = get_aos_settings_snapshot()
    configured = (
        settings.default_currency
        or _first_existing_doc("Currency", DEFAULT_CURRENCY_CANDIDATES)
        or DEFAULT_CURRENCY_CODE
    )
    currency_name, error = resolve_currency(configured)
    if error:
        return None, fail("System default currency is invalid.", error="CONFIG_ERROR")
    return currency_name, None


def _resolve_default_language(language: str | None):
    if language:
        return resolve_language(language)

    settings = get_aos_settings_snapshot()
    configured = (
        settings.default_language
        or _first_existing_doc("Language", DEFAULT_LANGUAGE_CANDIDATES)
        or DEFAULT_LANGUAGE_CODE
    )
    language_name, error = resolve_language(configured)
    if error:
        return None, fail("System default language is invalid.", error="CONFIG_ERROR")
    return language_name, None


def ensure_user_preference(
    user: str,
    *,
    country: str | None = None,
    currency: str | None = None,
    language: str | None = None,
):
    """Idempotently create and return an AOS User Preference.

    The helper repairs authenticated account rows missing required AOS preferences
    and makes login/me stable instead of crashing. Explicit request values are validated;
    otherwise configured AOS defaults are used. ``ignore_permissions=True`` is
    intentional because only auth/session/account code calls this after the user
    has been authenticated or immediately after creating the same user account.
    """

    if not user:
        return None, fail("User is required.", error="VALIDATION_ERROR")

    pref_name = frappe.db.get_value("AOS User Preference", {"user": user}, "name")
    if pref_name:
        return frappe.get_doc("AOS User Preference", pref_name), None

    country_name, error = _resolve_default_country(country)
    if error:
        return None, error

    currency_name, error = _resolve_default_currency(currency)
    if error:
        return None, error

    language_name, error = _resolve_default_language(language)
    if error:
        return None, error

    try:
        pref = frappe.new_doc("AOS User Preference")
        pref.user = user
        pref.country = country_name
        pref.currency = currency_name
        pref.language = language_name
        pref.insert(ignore_permissions=True)
        _clear_preference_cache(user)
        return pref, None
    except frappe.DuplicateEntryError:
        pref_name = frappe.db.get_value("AOS User Preference", {"user": user}, "name")
        if pref_name:
            return frappe.get_doc("AOS User Preference", pref_name), None
        return None, fail("Failed to initialize user preference.", error="PREFERENCE_CREATE_FAILED")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Preference Bootstrap Failed")
        return None, fail("Failed to initialize user preference.", error="PREFERENCE_CREATE_FAILED")


def ensure_auth_bootstrap(
    user: str,
    *,
    country: str | None = None,
    currency: str | None = None,
    language: str | None = None,
):
    """Ensure required AOS identity rows exist for a valid authenticated user."""

    ensure_aos_profile(user)
    return ensure_user_preference(user, country=country, currency=currency, language=language)


def safe_user_doc(user: str):
    if not user:
        return None
    return frappe.get_doc("User", user)


def user_exists_by_identifier(identifier: str) -> str | None:
    """Resolve a normalized login identifier to a Frappe User.name.

    Email lookup is preferred. Username lookup is only attempted on the User.name
    primary key and never on arbitrary fields.
    """

    identifier = (identifier or "").strip().lower()
    if not identifier:
        return None

    user_name = frappe.db.get_value("User", {"email": identifier}, "name")
    if user_name:
        return user_name

    if frappe.db.exists("User", identifier):
        return identifier

    return None


def safe_log_auth_event(title: str, *, identifier: str | None = None, user: str | None = None, reason: str | None = None) -> None:
    """Log auth events without passwords, tokens, cookies, or session IDs."""

    payload: dict[str, Any] = {"reason": reason or "unspecified"}
    if identifier:
        payload["identifier_hash"] = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]
    if user:
        payload["user_hash"] = hashlib.sha256(user.encode("utf-8")).hexdigest()[:16]

    try:
        frappe.logger("aos.auth").warning("%s %s", title, payload)
    except Exception:
        # Fallback to Error Log without sensitive request/session material.
        frappe.log_error(message=str(payload), title=title[:140])
