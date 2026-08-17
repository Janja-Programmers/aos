"""Recoverable account deletion and restoration endpoints.

Auth owns confirmation, OTP verification, and rate limiting. Accounts owns the
locked lifecycle transition and feature cleanup policy.
"""

from __future__ import annotations

import frappe

from aos.api.shared.account_status import can_restore_account, get_account_state
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.responses import fail, ok
from aos.services.accounts.constants import ACCOUNT_RESTORE_WINDOW_DAYS
from aos.services.accounts.errors import AccountError
from aos.services.accounts.lifecycle_service import AccountLifecycleService

from .constants import (
    DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_IP,
    DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_USER,
    RESTORE_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
    RESTORE_REQUEST_LIMIT_PER_HOUR_PER_IP,
    RESTORE_VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
    RESTORE_VERIFY_LIMIT_PER_HOUR_PER_IP,
)
from .otp_service import enforce_resend_cooldown, issue_otp, public_otp_invalid, verify_public_otp
from .validators import optional_string, require_email, require_otp, require_string
from .verification import ensure_ver_doc, get_ver_doc

RESTORE_PURPOSE = "account_restore"
DELETE_CONFIRMATION_TEXT = "DELETE"
DELETE_REASON_MAX_LEN = 300
RESTORE_REQUEST_GENERIC_MESSAGE = "If a restorable account exists for this email, a restore code has been sent."


def _normalize_reason(value: str | None):
    return optional_string(value, "reason", max_length=DELETE_REASON_MAX_LEN)


def _logout_current_session() -> None:
    try:
        frappe.local.login_manager.logout()
    except Exception:
        # All server sessions are already revoked by the lifecycle service. This
        # local cleanup is best effort and does not weaken the fail-closed boundary.
        pass


def delete_account_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    for key, limit in (
        (rate_limit_key("auth", "delete_account", "user", current_user), DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_USER),
        (rate_limit_key("auth", "delete_account", "ip", request_ip()), DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_IP),
    ):
        limited = rate_limit(key=key, ttl_seconds=3600, limit=limit, message="Too many delete-account attempts. Please try again later.")
        if limited:
            return limited
    confirmation, confirmation_err = require_string(kwargs.get("confirmation"), "confirmation", max_length=16)
    if confirmation_err:
        return confirmation_err
    if confirmation != DELETE_CONFIRMATION_TEXT:
        return fail("Please type DELETE to confirm account deletion.", error="VALIDATION_ERROR", data={"field": "confirmation"})
    reason, reason_err = _normalize_reason(kwargs.get("reason"))
    if reason_err:
        return reason_err
    try:
        result = AccountLifecycleService().delete(user=current_user, reason=reason or "")
        frappe.db.commit()
        _logout_current_session()
        return ok(
            "Account deleted successfully. You can restore it with email verification within the restore window.",
            data={"restore_window_days": ACCOUNT_RESTORE_WINDOW_DAYS, "cleanup": result.get("features", {}), **result},
        )
    except AccountError as exc:
        frappe.db.rollback()
        return safe_fail_from_exception(
            exc,
            fallback="Account operation could not be completed.",
            error=exc.code,
            http_status=exc.http_status,
        )
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "AOS Delete Account Failed")
        return fail("Failed to delete account. Please try again.", error="DELETE_ACCOUNT_FAILED", http_status=500)


def request_restore_account_impl(**kwargs):
    email, err = require_email(kwargs.get("email"))
    if err:
        return err
    for key, limit in (
        (rate_limit_key("auth", "restore_request", "identifier", email or "blank"), RESTORE_REQUEST_LIMIT_PER_HOUR_PER_EMAIL),
        (rate_limit_key("auth", "restore_request", "ip", request_ip()), RESTORE_REQUEST_LIMIT_PER_HOUR_PER_IP),
    ):
        limited = rate_limit(key=key, ttl_seconds=3600, limit=limit, message="Too many restore requests. Please try again later.")
        if limited:
            return limited
    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)
    state = get_account_state(user_name)
    if not state.get("is_deleted") or not state.get("can_restore"):
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)
    try:
        user = frappe.get_doc("User", user_name)
        ver = ensure_ver_doc(user_name, email=email, purpose=RESTORE_PURPOSE, for_update=True)
        cooldown = enforce_resend_cooldown(ver)
        if cooldown:
            return cooldown
        issue_otp(ver, email=email, full_name=user.first_name or user.full_name or "", purpose=RESTORE_PURPOSE, commit_before_send=True)
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "AOS Request Restore Account Failed")
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)


def restore_account_impl(**kwargs):
    email, email_err = require_email(kwargs.get("email"))
    if email_err:
        return email_err
    otp, otp_err = require_otp(kwargs.get("otp"))
    if otp_err:
        return otp_err
    for key, limit in (
        (rate_limit_key("auth", "restore_verify", "identifier", email or "blank"), RESTORE_VERIFY_LIMIT_PER_HOUR_PER_EMAIL),
        (rate_limit_key("auth", "restore_verify", "ip", request_ip()), RESTORE_VERIFY_LIMIT_PER_HOUR_PER_IP),
    ):
        limited = rate_limit(key=key, ttl_seconds=3600, limit=limit, message="Too many restore attempts. Please try again later.")
        if limited:
            return limited
    try:
        user_name = frappe.db.get_value("User", {"email": email}, "name")
        if not user_name:
            return public_otp_invalid()
        ver = get_ver_doc(user_name, purpose=RESTORE_PURPOSE, for_update=True)
        verified_err = verify_public_otp(ver, otp, consume=True)
        if verified_err:
            return verified_err
        state = get_account_state(user_name)
        if not state.get("is_deleted"):
            return fail("Account is not deleted.", error="ACCOUNT_NOT_DELETED")
        if not can_restore_account(user_name):
            return fail("This account can no longer be restored.", error="RESTORE_EXPIRED", data={"can_restore": False})
        result = AccountLifecycleService().restore(user=user_name)
        frappe.db.commit()
        return ok("Account restored successfully. Please login.", data={"can_login": True, "restore": result.get("features", {}), **result})
    except AccountError as exc:
        frappe.db.rollback()
        return safe_fail_from_exception(
            exc,
            fallback="Account operation could not be completed.",
            error=exc.code,
            http_status=exc.http_status,
        )
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "AOS Restore Account Failed")
        return fail("Failed to restore account. Please try again.", error="RESTORE_ACCOUNT_FAILED", http_status=500)
