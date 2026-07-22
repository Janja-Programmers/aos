from __future__ import annotations

import frappe

from aos.api.auth.account_helpers import ensure_user_preference
from aos.api.shared.auth import require_authenticated_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.accounts.http import set_private_no_store
from aos.services.localization_service import serialize_preference
from aos.services.user_preference_service import get_user_preference, is_country_locked

from .constants import GET_PREF_LIMIT_PER_MINUTE_PER_USER


def get_my_preference_impl(**_):
    current_user, err = require_authenticated_user()
    if err:
        return err
    set_private_no_store()
    rl = rate_limit(
        key=rate_limit_key("accounts", "preferences", "get", "user", current_user),
        ttl_seconds=60,
        limit=GET_PREF_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl
    try:
        pref = get_user_preference(current_user)
        repaired = False
        if not pref:
            doc, pref_err = ensure_user_preference(current_user)
            if pref_err:
                return pref_err
            pref = doc
            repaired = True
        if repaired:
            frappe.db.commit()
        return ok("Preferences loaded.", data=serialize_preference(pref, is_country_locked=is_country_locked(current_user)))
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Preferences Failed")
        return fail("Failed to fetch preferences.", error="INTERNAL_ERROR")
