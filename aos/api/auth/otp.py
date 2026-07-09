"""Email verification OTP endpoints."""

from __future__ import annotations

import frappe

from aos.api.shared.account_status import (
    can_restore_account,
    deleted_account_response,
    is_account_deleted,
)
from aos.api.shared.responses import ok, fail
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip

from .constants import (
    RESEND_LIMIT_PER_HOUR_PER_EMAIL,
    VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
)
from .validators import normalize_email, normalize_otp, validate_email
from .verification import get_ver_doc
from .otp_service import enforce_resend_cooldown, issue_otp, verify_otp


GENERIC_RESEND_MESSAGE = "If the account is pending verification, a new OTP has been sent."


def _deleted_account_block(user_name: str):
    if is_account_deleted(user_name):
        return deleted_account_response(restorable=can_restore_account(user_name))
    return None


def verify_email_otp_impl(**kwargs):
    email = normalize_email(kwargs.get("email") or "")
    otp = normalize_otp(kwargs.get("otp") or "")

    rl = rate_limit(
        key=rate_limit_key("auth", "verify_email", "identifier", email or request_ip()),
        ttl_seconds=60 * 60,
        limit=VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many verification attempts. Please try again later.",
    )
    if rl:
        return rl

    email_err = validate_email(email)
    if email_err:
        return email_err
    if not otp:
        return fail("OTP is required.", error="VALIDATION_ERROR", data={"field": "otp"})

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        # Do not leak whether the email is registered.
        return fail("Invalid OTP.", error="OTP_INVALID")

    deleted_err = _deleted_account_block(user_name)
    if deleted_err:
        return deleted_err

    ver = get_ver_doc(user_name, purpose="email_verification")
    if not ver:
        return fail("OTP not found. Please request a new OTP.", error="OTP_NOT_FOUND")

    err = verify_otp(ver, otp, consume=True)
    if err:
        return err

    frappe.db.set_value("User", user_name, "enabled", 1)
    return ok("Email verified. Account activated.")


def resend_email_otp_impl(**kwargs):
    email = normalize_email(kwargs.get("email") or "")

    email_err = validate_email(email)
    if email_err:
        return email_err

    rl = rate_limit(
        key=rate_limit_key("auth", "resend_email", "identifier", email),
        ttl_seconds=60 * 60,
        limit=RESEND_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many resend requests. Please try again later.",
    )
    if rl:
        return rl

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        return ok(GENERIC_RESEND_MESSAGE)

    deleted_err = _deleted_account_block(user_name)
    if deleted_err:
        return deleted_err

    user = frappe.get_doc("User", user_name)

    if int(user.enabled or 0) == 1:
        return ok("Account already active.")

    ver = get_ver_doc(user_name, purpose="email_verification")
    if not ver:
        return ok(GENERIC_RESEND_MESSAGE)

    cooldown = enforce_resend_cooldown(ver)
    if cooldown:
        return cooldown

    issue_otp(
        ver,
        email=email,
        full_name=user.first_name or "",
        purpose="email_verification",
    )

    return ok(GENERIC_RESEND_MESSAGE)
