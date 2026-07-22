from __future__ import annotations

import frappe

from aos.api.auth.account_helpers import ensure_user_preference
from aos.api.shared.auth import require_authenticated_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.accounts.http import set_private_no_store
from aos.services.accounts.observability import account_log
from aos.services.localization_service import serialize_preference
from aos.services.user_preference_service import is_country_locked, update_user_preference

from .constants import UPDATE_PREF_LIMIT_PER_MINUTE_PER_USER


def update_my_preference_impl(**kwargs):
    current_user, err = require_authenticated_user()
    if err:
        return err
    set_private_no_store()
    rl = rate_limit(
        key=rate_limit_key("accounts", "preferences", "update", "user", current_user),
        ttl_seconds=60,
        limit=UPDATE_PREF_LIMIT_PER_MINUTE_PER_USER,
        message="Too many updates. Please slow down.",
    )
    if rl:
        return rl
    allowed = {key: kwargs[key] for key in ("country", "currency", "language", "location") if key in kwargs}
    unknown = sorted(set(kwargs) - set(allowed))
    if unknown:
        return fail(f"Unsupported preference fields: {', '.join(unknown)}.", error="INVALID_PROFILE_FIELD")
    try:
        if not frappe.db.exists("AOS User Preference", {"user": current_user}):
            _doc, bootstrap_err = ensure_user_preference(current_user)
            if bootstrap_err:
                return bootstrap_err
        doc, update_err = update_user_preference(current_user, **allowed)
        if update_err:
            frappe.db.rollback()
            return update_err
        frappe.db.commit()
        account_log("account.preference.updated", user=current_user, changed_fields=allowed)
        return ok("Preference updated successfully.", data=serialize_preference(doc, is_country_locked=is_country_locked(current_user)))
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "AOS Update Preference Failed")
        return fail("Failed to update preference.", error="INTERNAL_ERROR")
