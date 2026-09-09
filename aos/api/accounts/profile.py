"""Thin Accounts profile API handlers."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.accounts.errors import AccountError, AccountValidationError
from aos.services.accounts.http import set_private_no_store
from aos.services.accounts.observability import account_log
from aos.services.accounts.profile_service import AccountProfileService

from .constants import (
    AVATAR_CHANGE_LIMIT_PER_HOUR_PER_USER,
    GET_MY_PROFILE_LIMIT_PER_MINUTE_PER_USER,
    GET_PUBLIC_PROFILE_LIMIT_PER_MINUTE_PER_USER,
    UPDATE_MY_PROFILE_LIMIT_PER_MINUTE_PER_USER,
)


def _failure(exc: AccountError):
    return safe_fail_from_exception(
        exc,
        fallback="Account request could not be completed.",
        error=exc.code,
        http_status=exc.http_status,
    )


def _reject_unknown_fields(kwargs: dict, *, allowed: set[str]) -> None:
    unknown = sorted(set(kwargs) - allowed)
    if unknown:
        raise AccountValidationError(
            f"Unsupported profile fields: {', '.join(unknown)}.",
            code="INVALID_PROFILE_FIELD",
        )


def _required_public_account_id(kwargs: dict) -> str:
    _reject_unknown_fields(kwargs, allowed={"account_id"})
    value = kwargs.get("account_id")
    if value in (None, ""):
        raise AccountValidationError("account_id is required.", code="ACCOUNT_ID_REQUIRED")
    return str(value).strip()


def get_my_profile_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    set_private_no_store()

    rl = rate_limit(
        key=rate_limit_key("accounts", "profile", "get", "my", "user", current_user),
        ttl_seconds=60,
        limit=GET_MY_PROFILE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many profile requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        _reject_unknown_fields(kwargs, allowed=set())
    except AccountError as exc:
        return _failure(exc)

    try:
        data = AccountProfileService().get_private_profile(user=current_user)
        account_log("account.profile.read", user=current_user)
        return ok("Profile fetched.", data=data)
    except AccountError as exc:
        return _failure(exc)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get My Profile Failed")
        return fail("Failed to fetch profile.", error="INTERNAL_ERROR")


def get_profile_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    set_private_no_store()

    rl = rate_limit(
        key=rate_limit_key("accounts", "profile", "get", "public", "user", current_user),
        ttl_seconds=60,
        limit=GET_PUBLIC_PROFILE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many public profile requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        target = _required_public_account_id(kwargs)
    except AccountError as exc:
        return _failure(exc)

    try:
        # Public profile payloads are viewer-relative (follow/block capabilities),
        # so authenticated responses must never be stored in a shared cache.
        data = AccountProfileService().get_public_profile(reference=target, viewer=current_user)
        account_log("account.profile.read", user=current_user)
        return ok("Profile fetched.", data=data)
    except AccountError as exc:
        return _failure(exc)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Public Profile Failed")
        return fail("Failed to fetch profile.", error="INTERNAL_ERROR")


def update_my_profile_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    set_private_no_store()

    rl = rate_limit(
        key=rate_limit_key("accounts", "profile", "update", "my", "user", current_user),
        ttl_seconds=60,
        limit=UPDATE_MY_PROFILE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many profile updates. Please slow down.",
    )
    if rl:
        return rl

    if "avatar_media_id" in kwargs or "remove_avatar" in kwargs:
        avatar_rl = rate_limit(
            key=rate_limit_key("accounts", "avatar", "update", "user", current_user),
            ttl_seconds=3600,
            limit=AVATAR_CHANGE_LIMIT_PER_HOUR_PER_USER,
            message="Too many avatar changes. Please try again later.",
        )
        if avatar_rl:
            return avatar_rl

    try:
        data = AccountProfileService().update_profile(user=current_user, payload=kwargs)
        return ok("Profile updated.", data=data)
    except AccountError as exc:
        frappe.db.rollback()
        return _failure(exc)
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "AOS Update My Profile Failed")
        return fail("Failed to update profile.", error="INTERNAL_ERROR")
