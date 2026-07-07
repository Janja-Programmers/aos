from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception

from aos.api.shared.validators import (
    resolve_country,
    resolve_language,
    resolve_currency,
)

from .constants import UPDATE_PREF_LIMIT_PER_MINUTE_PER_USER


def update_my_preference_impl(**kwargs):
    """Update current user's market preferences."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:preferences:update:user:{current_user}",
        ttl_seconds=60,
        limit=UPDATE_PREF_LIMIT_PER_MINUTE_PER_USER,
        message="Too many updates. Please slow down.",
    )
    if rl:
        return rl

    country_input = kwargs.get("country")
    language_input = kwargs.get("language")
    currency_input = kwargs.get("currency")

    if not country_input or not language_input or not currency_input:
        return fail("All fields are required.", code="VALIDATION_ERROR")

    try:
        # Resolve inputs
        country_id, err = resolve_country(country_input)
        if err:
            return err

        language_id, err = resolve_language(language_input)
        if err:
            return err

        currency_id, err = resolve_currency(currency_input)
        if err:
            return err

        # Fetch preference record
        pref = frappe.db.get_value(
            "AOS User Preference",
            {"user": current_user},
            ["name", "country"],
            as_dict=True,
        )

        old_country = pref.country if pref else None

        # Resolve seller for market lock check
        seller = frappe.db.get_value(
            "AOS Seller",
            {"user": current_user},
            "name",
        )

        # MARKET LOCK LOGIC
        if old_country and old_country != country_id:
            has_ads = False

            if seller:
                has_ads = bool(
                    frappe.db.exists(
                        "AOS Ad",
                        {"seller": seller},
                    )
                )

            if has_ads:
                return fail(
                    "You cannot change your market after creating ads.",
                    code="MARKET_LOCKED",
                )

        # Save Preference
        if pref:
            doc = frappe.get_doc("AOS User Preference", pref.name)
            doc.country = country_id
            doc.language = language_id
            doc.currency = currency_id
            doc.save(ignore_permissions=True)

        else:
            doc = frappe.new_doc("AOS User Preference")
            doc.user = current_user
            doc.country = country_id
            doc.language = language_id
            doc.currency = currency_id
            doc.insert(ignore_permissions=True)

        frappe.db.commit()

        return ok(
            "Preference updated successfully.",
            data={
                "country": doc.country,
                "language": doc.language,
                "currency": doc.currency,
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Update Preference Failed",
        )

        return fail(
            "Failed to update preference.",
            code="INTERNAL_ERROR",
        )
