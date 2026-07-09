from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.account_status import can_restore_account, deleted_account_response, is_account_deleted
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok, fail

from .constants import (
    FORGOT_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
    FORGOT_VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
    FORGOT_RESET_LIMIT_PER_HOUR_PER_EMAIL,
)
from .validators import require_email, require_otp, require_password, require_token, validate_password_strength
from .verification import compute_reset_token_expiry, ensure_ver_doc, generate_reset_token, get_ver_doc, otp_hash
from .otp_service import enforce_resend_cooldown, issue_otp, public_otp_invalid, verify_public_otp


PURPOSE = "password_reset"
GENERIC_REQUEST_MESSAGE = "If an account exists for this email, an OTP has been sent."


def _deleted_account_block(user_name: str):
    if is_account_deleted(user_name):
        return deleted_account_response(restorable=can_restore_account(user_name))
    return None


def forgot_password_request_impl(**kwargs):
    email, email_err = require_email(kwargs.get("email"))
    if email_err:
        return email_err

    rl = rate_limit(
        key=rate_limit_key("auth", "forgot_password_request", "identifier", email),
        ttl_seconds=60 * 60,
        limit=FORGOT_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many requests. Please try again later.",
    )
    if rl:
        return rl

    rl2 = rate_limit(
        key=rate_limit_key("auth", "forgot_password_request", "ip", request_ip()),
        ttl_seconds=60 * 60,
        limit=FORGOT_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many requests. Please try again later.",
    )
    if rl2:
        return rl2

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name or is_account_deleted(user_name):
        return ok(GENERIC_REQUEST_MESSAGE)

    user = frappe.get_doc("User", user_name)
    ver = ensure_ver_doc(user_name, email=email, purpose=PURPOSE)

    cooldown = enforce_resend_cooldown(ver)
    if cooldown:
        return cooldown

    ver.reset_token_hash = ""
    ver.reset_token_expires_at = None
    ver.save(ignore_permissions=True)

    try:
        issue_otp(
            ver,
            email=email,
            full_name=user.first_name or "",
            purpose=PURPOSE,
            commit_before_send=True,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Forgot Password OTP Failed")
        return ok(GENERIC_REQUEST_MESSAGE)

    return ok(GENERIC_REQUEST_MESSAGE)


def forgot_password_verify_otp_impl(**kwargs):
    email, email_err = require_email(kwargs.get("email"))
    if email_err:
        return email_err

    otp, otp_err = require_otp(kwargs.get("otp"))
    if otp_err:
        return otp_err

    rl = rate_limit(
        key=rate_limit_key("auth", "forgot_password_verify", "identifier", email),
        ttl_seconds=60 * 60,
        limit=FORGOT_VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many attempts. Please try again later.",
    )
    if rl:
        return rl

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        return public_otp_invalid()

    if is_account_deleted(user_name):
        # Do not reveal deleted/restorable state through password-reset OTP.
        return public_otp_invalid()

    ver = get_ver_doc(user_name, purpose=PURPOSE)
    err = verify_public_otp(ver, otp, consume=True)
    if err:
        return err

    token = generate_reset_token()
    ver.reset_token_hash = otp_hash(token)
    ver.reset_token_expires_at = compute_reset_token_expiry()
    ver.save(ignore_permissions=True)

    return ok("OTP verified.", data={"reset_token": token})


def forgot_password_reset_impl(**kwargs):
    email, email_err = require_email(kwargs.get("email"))
    if email_err:
        return email_err

    reset_token, token_err = require_token(kwargs.get("reset_token"), "reset_token")
    if token_err:
        return token_err

    new_password, new_password_err = require_password(kwargs.get("new_password"), "new_password")
    if new_password_err:
        return new_password_err

    confirm_password, confirm_password_err = require_password(kwargs.get("confirm_password"), "confirm_password")
    if confirm_password_err:
        return confirm_password_err

    rl = rate_limit(
        key=rate_limit_key("auth", "forgot_password_reset", "identifier", email),
        ttl_seconds=60 * 60,
        limit=FORGOT_RESET_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many attempts. Please try again later.",
    )
    if rl:
        return rl

    if new_password != confirm_password:
        return fail("Passwords do not match.", error="PASSWORD_MISMATCH")

    pw_err = validate_password_strength(new_password)
    if pw_err:
        return pw_err

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        return fail("Invalid reset token.", error="TOKEN_INVALID")

    deleted_err = _deleted_account_block(user_name)
    if deleted_err:
        return deleted_err

    ver = get_ver_doc(user_name, purpose=PURPOSE)
    if not ver or not getattr(ver, "reset_token_hash", None):
        return fail("Invalid reset token.", error="TOKEN_INVALID")

    if not ver.reset_token_expires_at or now_datetime() > ver.reset_token_expires_at:
        return fail("Reset token expired. Please request a new OTP.", error="TOKEN_EXPIRED")

    if otp_hash(reset_token) != ver.reset_token_hash:
        return fail("Invalid reset token.", error="TOKEN_INVALID")

    try:
        user = frappe.get_doc("User", user_name)
        user.new_password = new_password
        user.flags.ignore_password_policy = True
        user.save(ignore_permissions=True)

        ver.reset_token_hash = ""
        ver.reset_token_expires_at = None
        ver.save(ignore_permissions=True)

        frappe.db.commit()
        return ok("Password updated successfully.")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Password Reset Failed")
        frappe.db.rollback()
        return fail("Could not reset password. Please try again.", error="INTERNAL_ERROR")
