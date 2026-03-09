from __future__ import annotations

import frappe

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail

from .constants import LOCALE_BUNDLE_LIMIT_PER_MIN_PER_IP


def _get_countries():
    rows = frappe.get_all(
        "Country",
        fields=["name", "code"],
        order_by="name asc",
        limit_page_length=1000,
    )

    return [
        {
            "name": r["name"],
            "code": (r.get("code") or "") or None,
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
            "name": r["name"],
            "symbol": r.get("symbol") or None,
        }
        for r in rows
    ]


def get_locale_bundle_impl(**_):
    """
    Returns available countries, languages and currencies.

    Defaults are NOT returned.
    Backend applies defaults automatically if client does not send values.
    """

    rl = rate_limit(
        key=f"aos:locale:bundle:ip:{request_ip()}",
        ttl_seconds=60,
        limit=LOCALE_BUNDLE_LIMIT_PER_MIN_PER_IP,
        message="Too many requests. Please try again later.",
    )

    if rl:
        return rl

    try:
        data = {
            "countries": _get_countries(),
            "languages": _get_languages(),
            "currencies": _get_currencies(),
        }

        return ok(
            "Locale bundle loaded.",
            data=data,
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Locale Bundle Failed",
        )

        return fail(
            "Failed to load locale bundle.",
            code="INTERNAL_ERROR",
        )
