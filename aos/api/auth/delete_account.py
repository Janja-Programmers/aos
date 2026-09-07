"""Recoverable account deletion and restoration endpoints.

Auth owns confirmation, OTP verification, and rate limiting. Accounts owns the
locked lifecycle transition and feature cleanup policy.
"""

from __future__ import annotations

import frappe

from aos.api.shared.account_status import can_restore_account, get_account_state
from aos.api.shared.auth import require_login
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.responses import fail, ok
from aos.services.accounts.constants import ACCOUNT_RESTORE_WINDOW_DAYS
from aos.services.accounts.errors import AccountError
from aos.services.accounts.lifecycle_service import AccountLifecycleService

from .account_helpers import user_for_email
from .constants import (
    DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_IP,
    DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_USER,
    RESTORE_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
    RESTORE_REQUEST_LIMIT_PER_HOUR_PER_IP,
    RESTORE_VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
    RESTORE_VERIFY_LIMIT_PER_HOUR_PER_IP,
)
from .otp_service import issue_otp, public_otp_invalid, resend_allowed, verify_public_otp
from .contracts import reject_unknown_fields
from .locking import lock_user
from .rate_limits import auth_ip_limit, auth_rate_limit
from .validators import optional_string, require_email, require_otp, require_string
from .verification import ensure_ver_doc, get_ver_doc

RESTORE_PURPOSE = "account_restore"
DELETE_CONFIRMATION_TEXT = "DELETE"
DELETE_REASON_MAX_LEN = 300
RESTORE_REQUEST_GENERIC_MESSAGE = "If a restorable account exists for this email, a restore code has been sent."


def _normalize_reason(value: str | None):
    return optional_string(value, "reason", max_length=DELETE_REASON_MAX_LEN)


def delete_account_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"confirmation", "reason"})
    if unknown:
        return unknown
    current_user, err = require_login()
    if err:
        return err
    limited = auth_rate_limit(
        operation="delete_account", dimension="user", value=current_user,
        limit=DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_USER, message="Too many delete-account attempts. Please try again later.",
    ) or auth_ip_limit(
        operation="delete_account", limit=DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_IP,
        message="Too many delete-account attempts. Please try again later.",
    )
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
        lock_user(current_user)
        result = AccountLifecycleService().delete(user=current_user, reason=reason or "")
        frappe.local.login_manager.clear_cookies()
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
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Delete Account Failed", exc, operation="delete_account")
        return fail("Failed to delete account. Please try again.", error="DELETE_ACCOUNT_FAILED", http_status=500)


def request_restore_account_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"email"})
    if unknown:
        return unknown
    email, err = require_email(kwargs.get("email"))
    if err:
        return err
    limited = auth_rate_limit(
        operation="restore_request", dimension="identifier", value=email,
        limit=RESTORE_REQUEST_LIMIT_PER_HOUR_PER_EMAIL, message="Too many restore requests. Please try again later.",
    ) or auth_ip_limit(
        operation="restore_request", limit=RESTORE_REQUEST_LIMIT_PER_HOUR_PER_IP,
        message="Too many restore requests. Please try again later.",
    )
    if limited:
        return limited
    user_name = user_for_email(email)
    if not user_name:
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)
    state = get_account_state(user_name)
    if not state.get("is_deleted") or not state.get("can_restore"):
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)
    try:
        lock_user(user_name)
        user = frappe.get_doc("User", user_name)
        ver = ensure_ver_doc(user_name, email=email, purpose=RESTORE_PURPOSE, for_update=True)
        if not resend_allowed(ver):
            return ok(RESTORE_REQUEST_GENERIC_MESSAGE)
        issue_otp(ver, email=email, full_name=user.first_name or user.full_name or "", purpose=RESTORE_PURPOSE)
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Request Restore Account Failed", exc, operation="restore_request")
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)


def restore_account_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"email", "otp"})
    if unknown:
        return unknown
    email, email_err = require_email(kwargs.get("email"))
    if email_err:
        return email_err
    otp, otp_err = require_otp(kwargs.get("otp"))
    if otp_err:
        return otp_err
    limited = auth_rate_limit(
        operation="restore_verify", dimension="identifier", value=email,
        limit=RESTORE_VERIFY_LIMIT_PER_HOUR_PER_EMAIL, message="Too many restore attempts. Please try again later.",
    ) or auth_ip_limit(
        operation="restore_verify", limit=RESTORE_VERIFY_LIMIT_PER_HOUR_PER_IP,
        message="Too many restore attempts. Please try again later.",
    )
    if limited:
        return limited
    try:
        user_name = user_for_email(email)
        if not user_name:
            return verify_public_otp(None, otp)
        lock_user(user_name)
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
        return ok("Account restored successfully. Please login.", data={"can_login": True, "restore": result.get("features", {}), **result})
    except AccountError as exc:
        frappe.db.rollback()
        return safe_fail_from_exception(
            exc,
            fallback="Account operation could not be completed.",
            error=exc.code,
            http_status=exc.http_status,
        )
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Restore Account Failed", exc, operation="restore_verify")
        return fail("Failed to restore account. Please try again.", error="RESTORE_ACCOUNT_FAILED", http_status=500)
