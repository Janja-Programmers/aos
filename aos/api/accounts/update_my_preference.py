from __future__ import annotations

import frappe

from aos.api.auth.account_helpers import _clear_preference_cache
from aos.api.shared.auth import require_authenticated_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception

from aos.services.localization_service import (
    is_country_locked,
    serialize_preference,
    validate_country,
    validate_currency,
    validate_language,
)

from .constants import UPDATE_PREF_LIMIT_PER_MINUTE_PER_USER


def update_my_preference_impl(**kwargs):
    """Update current user's market preferences."""

    current_user, err = require_authenticated_user()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("accounts", "preferences", "update", "user", current_user),
        ttl_seconds=60,
        limit=UPDATE_PREF_LIMIT_PER_MINUTE_PER_USER,
        message="Too many updates. Please slow down.",
    )
    if rl:
        return rl

    country_input = kwargs.get("country")
    language_input = kwargs.get("language")
    currency_input = kwargs.get("currency")

    if all(value in (None, "") for value in (country_input, language_input, currency_input)):
        return fail("At least one preference field is required.", error="VALIDATION_ERROR")

    try:
        pref = frappe.db.get_value(
            "AOS User Preference",
            {"user": current_user},
            ["name", "country", "currency", "language"],
            as_dict=True,
        )

        if not pref:
            from aos.api.auth.account_helpers import ensure_user_preference
            doc, err = ensure_user_preference(current_user)
            if err:
                return err
            pref = frappe._dict(name=doc.name, country=doc.country, currency=doc.currency, language=doc.language)

        country_id, err = validate_country(country_input) if country_input not in (None, "") else (pref.country, None)
        if err:
            return err
        currency_id, err = validate_currency(currency_input) if currency_input not in (None, "") else (pref.currency, None)
        if err:
            return err
        language_id, err = validate_language(language_input) if language_input not in (None, "") else (pref.language, None)
        if err:
            return err

        if pref.country != country_id:
            if is_country_locked(current_user):
                return fail(
                    "You cannot change your market after creating ads.",
                    error="MARKET_LOCKED",
                )

        doc = frappe.get_doc("AOS User Preference", pref.name)
        doc.country, doc.language, doc.currency = country_id, language_id, currency_id
        doc.save(ignore_permissions=True)

        _clear_preference_cache(current_user)
        frappe.db.commit()

        return ok(
            "Preference updated successfully.",
            data=serialize_preference(doc, is_country_locked=is_country_locked(current_user)),
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Update Preference Failed")
        return fail("Failed to update preference.", error="INTERNAL_ERROR")
