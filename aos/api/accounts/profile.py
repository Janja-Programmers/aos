"""Thin Accounts profile API handlers."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.accounts.errors import AccountError
from aos.services.accounts.http import set_private_no_store, set_public_cache
from aos.services.accounts.observability import account_log
from aos.services.accounts.profile_service import AccountProfileService

from .constants import GET_PROFILE_LIMIT_PER_MINUTE_PER_USER, UPDATE_PROFILE_LIMIT_PER_MINUTE_PER_USER


def _failure(exc: AccountError):
    return fail(str(exc), error=exc.code, http_status=exc.http_status)


def get_profile_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    rl = rate_limit(
        key=rate_limit_key("accounts", "profile", "get", "user", current_user),
        ttl_seconds=60,
        limit=GET_PROFILE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many profile requests. Please try again shortly.",
    )
    if rl:
        return rl

    target = kwargs.get("target_user") or kwargs.get("account_id")
    try:
        service = AccountProfileService()
        if not target:
            set_private_no_store()
            data = service.get_private_profile(user=current_user)
        else:
            data = service.get_public_profile(reference=target, viewer=current_user)
            set_public_cache()
        account_log("account.profile.read", user=current_user)
        return ok("Profile fetched.", data=data)
    except AccountError as exc:
        return _failure(exc)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Profile Failed")
        return fail("Failed to fetch profile.", error="INTERNAL_ERROR")


def update_profile_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    set_private_no_store()
    rl = rate_limit(
        key=rate_limit_key("accounts", "profile", "update", "user", current_user),
        ttl_seconds=60,
        limit=UPDATE_PROFILE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many profile updates. Please slow down.",
    )
    if rl:
        return rl
    try:
        data = AccountProfileService().update_profile(user=current_user, payload=kwargs)
        frappe.db.commit()
        return ok("Profile updated.", data=data)
    except AccountError as exc:
        frappe.db.rollback()
        return _failure(exc)
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "AOS Update Profile Failed")
        return fail("Failed to update profile.", error="INTERNAL_ERROR")
