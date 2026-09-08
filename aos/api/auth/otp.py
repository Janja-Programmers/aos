"""Email verification OTP endpoints."""

from __future__ import annotations

import frappe

from aos.api.shared.responses import ok

from .account_helpers import profile_display_name, user_for_email
from .constants import RESEND_LIMIT_PER_HOUR_PER_EMAIL, VERIFY_LIMIT_PER_HOUR_PER_EMAIL
from .contracts import reject_unknown_fields
from .locking import lock_user
from .otp_service import dummy_otp_issue_work, issue_otp, public_otp_invalid, resend_allowed, verify_public_otp
from .rate_limits import auth_ip_limit, auth_rate_limit
from .validators import require_email, require_otp
from .verification import EMAIL_VERIFICATION_PURPOSE, ensure_ver_doc, get_ver_doc

GENERIC_RESEND_MESSAGE = "If verification is required for this email, a code has been queued."


def verify_email_otp_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"email", "otp"})
    if unknown:
        return unknown
    email, err = require_email(kwargs.get("email"))
    if err:
        return err
    otp, err = require_otp(kwargs.get("otp"))
    if err:
        return err
    limited = auth_rate_limit(
        operation="verify_email",
        dimension="identifier",
        value=email,
        limit=VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many attempts. Please try again later.",
    ) or auth_ip_limit(operation="verify_email", limit=120, message="Too many attempts. Please try again later.")
    if limited:
        return limited
    user_name = user_for_email(email)
    if not user_name:
        return verify_public_otp(None, otp)
    try:
        lock_user(user_name)
        ver = get_ver_doc(user_name, EMAIL_VERIFICATION_PURPOSE, for_update=True)
        verified = verify_public_otp(ver, otp, consume=True)
        if verified:
            return verified
        user = frappe.get_doc("User", user_name)
        if int(user.enabled or 0) != 1:
            user.enabled = 1
            user.save(ignore_permissions=True)
        return ok("Email verified. Account activated.")
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Email OTP Verify Failed", exc, operation="verify_email_otp")
        return public_otp_invalid()


def resend_email_otp_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"email"})
    if unknown:
        return unknown
    email, err = require_email(kwargs.get("email"))
    if err:
        return err
    limited = auth_rate_limit(
        operation="resend_email",
        dimension="identifier",
        value=email,
        limit=RESEND_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many requests. Please try again later.",
    ) or auth_ip_limit(operation="resend_email", limit=60, message="Too many requests. Please try again later.")
    if limited:
        return limited
    user_name = user_for_email(email)
    if not user_name:
        dummy_otp_issue_work()
        return ok(GENERIC_RESEND_MESSAGE)
    try:
        lock_user(user_name)
        if int(frappe.db.get_value("User", user_name, "enabled") or 0) == 1:
            dummy_otp_issue_work()
            return ok(GENERIC_RESEND_MESSAGE)
        ver = ensure_ver_doc(user_name, purpose=EMAIL_VERIFICATION_PURPOSE, for_update=True)
        if not resend_allowed(ver):
            dummy_otp_issue_work()
            return ok(GENERIC_RESEND_MESSAGE)
        full_name = profile_display_name(user_name)
        issue_otp(ver, email=email, full_name=full_name, purpose=EMAIL_VERIFICATION_PURPOSE)
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Resend Email OTP Failed", exc, operation="resend_email_otp")
    return ok(GENERIC_RESEND_MESSAGE)
