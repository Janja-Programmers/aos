"""Enumeration-safe password recovery lifecycle."""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.account_status import is_account_deleted
from aos.api.shared.responses import fail, ok

from .account_helpers import profile_display_name, user_for_email
from .constants import (
    FORGOT_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
    FORGOT_RESET_LIMIT_PER_HOUR_PER_EMAIL,
    FORGOT_VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
)
from .contracts import reject_unknown_fields
from .locking import lock_user
from .otp_service import issue_otp, public_otp_invalid, resend_allowed, verify_public_otp
from .passwords import set_user_password, validate_new_password
from .rate_limits import auth_ip_limit, auth_rate_limit
from .session_control import revoke_all_sessions
from .validators import require_email, require_otp, require_password, require_token
from .verification import (
    PASSWORD_RESET_PURPOSE,
    compute_continuation_expiry,
    ensure_ver_doc,
    generate_continuation_token,
    get_ver_doc,
    token_digest,
    token_matches,
)

GENERIC_REQUEST_MESSAGE = "If an account exists for this email, a recovery code has been queued."


def _identifier_limit(operation: str, email: str, limit: int, message: str):
    return auth_rate_limit(
        operation=operation,
        dimension="identifier",
        value=email,
        limit=limit,
        message=message,
    ) or auth_ip_limit(operation=operation, limit=max(limit * 6, 60), message=message)


def forgot_password_request_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"email"})
    if unknown:
        return unknown
    email, err = require_email(kwargs.get("email"))
    if err:
        return err
    limited = _identifier_limit("forgot_request", email, FORGOT_REQUEST_LIMIT_PER_HOUR_PER_EMAIL, "Too many requests. Please try again later.")
    if limited:
        return limited
    user_name = user_for_email(email)
    if not user_name or is_account_deleted(user_name):
        return ok(GENERIC_REQUEST_MESSAGE)
    try:
        lock_user(user_name)
        ver = ensure_ver_doc(user_name, purpose=PASSWORD_RESET_PURPOSE, for_update=True)
        if not resend_allowed(ver):
            return ok(GENERIC_REQUEST_MESSAGE)
        ver.continuation_token_hash = ""
        ver.continuation_expires_at = None
        ver.save(ignore_permissions=True)
        full_name = profile_display_name(user_name)
        issue_otp(ver, email=email, full_name=full_name, purpose=PASSWORD_RESET_PURPOSE)
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Forgot Password Request Failed", exc, operation="forgot_password_request")
    return ok(GENERIC_REQUEST_MESSAGE)


def forgot_password_verify_otp_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"email", "otp"})
    if unknown:
        return unknown
    email, err = require_email(kwargs.get("email"))
    if err:
        return err
    otp, err = require_otp(kwargs.get("otp"))
    if err:
        return err
    limited = _identifier_limit("forgot_verify", email, FORGOT_VERIFY_LIMIT_PER_HOUR_PER_EMAIL, "Too many attempts. Please try again later.")
    if limited:
        return limited
    user_name = user_for_email(email)
    if not user_name or is_account_deleted(user_name):
        return verify_public_otp(None, otp)
    try:
        lock_user(user_name)
        ver = get_ver_doc(user_name, purpose=PASSWORD_RESET_PURPOSE, for_update=True)
        verified = verify_public_otp(ver, otp, consume=True)
        if verified:
            return verified
        token = generate_continuation_token()
        ver.continuation_token_hash = token_digest(token)
        ver.continuation_expires_at = compute_continuation_expiry()
        ver.save(ignore_permissions=True)
        return ok("OTP verified.", data={"reset_token": token})
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Forgot Password OTP Verify Failed", exc, operation="forgot_password_verify")
        return public_otp_invalid()


def forgot_password_reset_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"email", "reset_token", "new_password", "confirm_password"})
    if unknown:
        return unknown
    email, err = require_email(kwargs.get("email"))
    if err:
        return err
    reset_token, err = require_token(kwargs.get("reset_token"), "reset_token")
    if err:
        return err
    new_password, err = require_password(kwargs.get("new_password"), "new_password")
    if err:
        return err
    confirm_password, err = require_password(kwargs.get("confirm_password"), "confirm_password")
    if err:
        return err
    limited = _identifier_limit("forgot_reset", email, FORGOT_RESET_LIMIT_PER_HOUR_PER_EMAIL, "Too many attempts. Please try again later.")
    if limited:
        return limited
    if new_password != confirm_password:
        return fail("Passwords do not match.", error="PASSWORD_MISMATCH")
    user_name = user_for_email(email)
    if not user_name or is_account_deleted(user_name):
        return fail("Invalid reset token.", error="TOKEN_INVALID")
    try:
        # User first, verification second is the canonical auth lock order.
        lock_user(user_name)
        ver = get_ver_doc(user_name, purpose=PASSWORD_RESET_PURPOSE, for_update=True)
        if not ver or not getattr(ver, "continuation_token_hash", None):
            return fail("Invalid reset token.", error="TOKEN_INVALID")
        if not ver.continuation_expires_at or now_datetime() > ver.continuation_expires_at:
            return fail("Reset token expired. Please request a new code.", error="TOKEN_EXPIRED")
        if not token_matches(reset_token, str(ver.continuation_token_hash)):
            return fail("Invalid reset token.", error="TOKEN_INVALID")

        policy = validate_new_password(user_name, new_password)
        if policy:
            return policy
        set_user_password(user_name, new_password)
        ver.continuation_token_hash = ""
        ver.continuation_expires_at = None
        ver.save(ignore_permissions=True)
        revoke_all_sessions(user_name)
        return ok("Password updated successfully. Please login again.")
    except frappe.ValidationError:
        frappe.db.rollback()
        return fail("Password does not meet the required policy.", error="VALIDATION_ERROR", data={"field": "new_password"})
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Password Reset Failed", exc, operation="forgot_password_reset")
        return fail("Could not reset password. Please try again.", error="INTERNAL_ERROR")
