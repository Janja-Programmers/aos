from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import GET_PREF_LIMIT_PER_MINUTE_PER_USER


def get_my_preference_impl(**_):
    """Fetch current user's market preferences."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:preferences:get:user:{current_user}",
        ttl_seconds=60,
        limit=GET_PREF_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )

    if rl:
        return rl

    try:
        doc = frappe.db.get_value(
            "AOS User Preference",
            {"user": current_user},
            ["country", "language", "currency"],
            as_dict=True,
        )

        if not doc:
            return fail("Preference not set.", code="NOT_FOUND")

        # Load linked docs
        country = frappe.get_doc("Country", doc.country)
        language = frappe.get_doc("Language", doc.language)
        currency = frappe.get_doc("Currency", doc.currency)

        return ok(
            "Preferences loaded.",
            data={
                "country": {
                    "name": country.country_name,
                    "code": country.code,
                },
                "language": {
                    "name": language.language_name,
                    "code": language.name,
                },
                "currency": {
                    "name": currency.currency_name,
                    "symbol": currency.symbol,
                },
            },
        )

    except frappe.DoesNotExistError:
        return fail("Preference data invalid.", code="DATA_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Preferences Failed",
        )

        return fail(
            "Failed to fetch preferences.",
            code="INTERNAL_ERROR",
        )
