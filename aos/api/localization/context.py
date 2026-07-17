from __future__ import annotations

import frappe

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.auth import current_user
from aos.services.localization_service import (
    get_default_preferences,
    resolve_accept_language,
    serialize_context,
    validate_country,
    validate_currency,
    validate_language,
)


def _header(name: str) -> str:
    request = getattr(frappe.local, "request", None)
    return str(request.headers.get(name) or "").strip() if request else ""


def resolve_effective_preference_context(*, country=None, currency=None, language=None):
    user = current_user()
    if user and user != "Guest":
        preference = frappe.db.get_value("AOS User Preference", {"user": user}, ["country", "currency", "language"], as_dict=True)
        if not preference:
            return None, fail("User preference not configured.", error="CONFIG_ERROR")
        return {**preference, "sources": {field: "user_preference" for field in ("country", "currency", "language")}}, None

    return resolve_guest_preference_context(country=country, currency=currency, language=language)


def resolve_guest_preference_context(*, country=None, currency=None, language=None):
    """Resolve guest/auth-bootstrap values from inputs, headers, then defaults."""

    defaults, error = get_default_preferences()
    if error:
        return None, error
    system_defaults = dict(defaults)
    sources = {field: "default" for field in defaults}
    for field, value, validator in (("country", country, validate_country), ("currency", currency, validate_currency), ("language", language, validate_language)):
        if value not in (None, ""):
            defaults[field], error = validator(value)
            if error:
                return None, error
            sources[field] = "request"
    if country in (None, ""):
        geo_country, geo_error = validate_country(_header("X-Country-Code") or _header("CF-IPCountry"), required=False)
        if geo_country and not geo_error:
            defaults["country"] = geo_country
            sources["country"] = "geoip"
        else:
            defaults["country"] = system_defaults["country"]
    if language in (None, ""):
        accepted = resolve_accept_language(_header("Accept-Language"))
        if accepted:
            defaults["language"], sources["language"] = accepted, "accept_language"
    return {**defaults, "sources": sources}, None


def resolve_preference_context_impl(**kwargs):
    limited = rate_limit(key=f"aos:locale:context:ip:{request_ip()}", ttl_seconds=60, limit=120, message="Too many requests. Please try again later.")
    if limited:
        return limited
    try:
        context, error = resolve_effective_preference_context(country=kwargs.get("country"), currency=kwargs.get("currency"), language=kwargs.get("language"))
        if error:
            return error
        return ok("Preference context resolved.", data=serialize_context(context))
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Resolve Preference Context Failed")
        return fail("Failed to resolve preference context.", error="INTERNAL_ERROR")
