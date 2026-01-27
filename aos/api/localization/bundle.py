from __future__ import annotations

import frappe

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok
from aos.utils.aos_settings import get_aos_settings_snapshot

from .constants import LOCALE_BUNDLE_LIMIT_PER_MIN_PER_IP


def _get_countries():
    # Country is a core DocType. Keep payload stable for mobile clients.
    rows = frappe.get_all(
        "Country",
        fields=["name", "code"],
        order_by="name asc",
        limit_page_length=1000,
    )
    return [
        {
            "name": r["name"],
            "code": (r.get("code") or "").upper() or None,
        }
        for r in rows
    ]


def _get_languages():
    rows = frappe.get_all(
        "Language",
        fields=["language_name", "language_code"],
        order_by="name asc",
        limit_page_length=1000,
    )
    return [
        {
            "name": r["language_name"],
            "code": (r.get("language_code") or "").lower() or None,
        }
        for r in rows
    ]


def _get_currencies():
    rows = frappe.get_all(
        "Currency",
        fields=["name", "symbol"],
        order_by="name asc",
        limit_page_length=1000,
    )
    return [
        {
            "code": r["name"],
            "symbol": r.get("symbol") or None,
        }
        for r in rows
    ]


def get_locale_bundle_impl():
    # Rate limit by IP (guest-safe)
    rl = rate_limit(
        key=f"aos:locale:bundle:ip:{request_ip()}",
        ttl_seconds=60,
        limit=LOCALE_BUNDLE_LIMIT_PER_MIN_PER_IP,
        message="Too many requests. Please try again later.",
    )
    if rl:
        return rl

    settings = get_aos_settings_snapshot()

    data = {
        "settings": {
            "base_currency": settings.base_currency,
            "default_language": settings.default_language,
            "default_country": settings.default_country,
            "enable_all_countries": settings.enable_all_countries,
            "enable_all_languages": settings.enable_all_languages,
        },
        "countries": _get_countries() if settings.enable_all_countries else [],
        "languages": _get_languages() if settings.enable_all_languages else [],
        "currencies": _get_currencies(),
    }

    return ok("Locale bundle loaded.", data)
