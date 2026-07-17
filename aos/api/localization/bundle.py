from __future__ import annotations

import frappe

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from .constants import LOCALE_BUNDLE_LIMIT_PER_MIN_PER_IP
from aos.services.localization_service import get_all_countries, get_default_preferences, get_enabled_currencies, get_enabled_languages


def get_locale_bundle_impl(**_):
    limited = rate_limit(key=f"aos:locale:bundle:ip:{request_ip()}", ttl_seconds=60, limit=LOCALE_BUNDLE_LIMIT_PER_MIN_PER_IP, message="Too many requests. Please try again later.")
    if limited:
        return limited
    try:
        defaults, error = get_default_preferences()
        if error:
            return error
        return ok("Locale bundle fetched.", data={"countries": get_all_countries(), "currencies": get_enabled_currencies(defaults["currency"]), "languages": get_enabled_languages(defaults["language"]), "defaults": defaults})
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Locale Bundle Failed")
        return fail("Failed to load locale bundle.", error="INTERNAL_ERROR")
