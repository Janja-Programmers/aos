from __future__ import annotations

import frappe

from aos.api.auth.account_helpers import ensure_user_preference
from aos.api.shared.auth import require_authenticated_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail

from .constants import GET_PREF_LIMIT_PER_MINUTE_PER_USER


def _serialize_preference_doc(doc):
    country = frappe.get_doc("Country", doc.country)
    language = frappe.get_doc("Language", doc.language)
    currency = frappe.get_doc("Currency", doc.currency)

    return {
        "country": {
            "id": doc.country,
            "name": country.country_name,
            "code": country.code,
        },
        "language": {
            "id": doc.language,
            "name": language.language_name,
            "code": language.name,
        },
        "currency": {
            "id": doc.currency,
            "name": currency.currency_name,
            "code": currency.name,
            "symbol": currency.symbol,
        },
    }


def get_my_preference_impl(**_):
    """Fetch current user's market preferences.

    Uses require_authenticated_user() instead of require_login() so authenticated users
    with a missing preference can be repaired safely.
    """

    current_user, err = require_authenticated_user()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("accounts", "preferences", "get", "user", current_user),
        ttl_seconds=60,
        limit=GET_PREF_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        doc, pref_err = ensure_user_preference(current_user)
        if pref_err:
            return pref_err

        return ok("Preferences loaded.", data=_serialize_preference_doc(doc))

    except frappe.DoesNotExistError:
        return fail("Preference data invalid.", error="DATA_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Preferences Failed")
        return fail("Failed to fetch preferences.", error="INTERNAL_ERROR")
