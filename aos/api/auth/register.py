"""Atomic AOS email/password registration."""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail, ok

from .account_helpers import create_auth_bootstrap, safe_log_auth_event, user_for_email
from .constants import REGISTER_LIMIT_PER_HOUR_PER_EMAIL, REGISTER_LIMIT_PER_HOUR_PER_IP
from .contracts import reject_unknown_fields
from .otp_service import issue_otp
from .rate_limits import auth_ip_limit, auth_rate_limit
from .user_controller import mark_aos_managed_website_user_creation
from .validators import optional_bootstrap_inputs, validate_registration_inputs
from .verification import EMAIL_VERIFICATION_PURPOSE, ensure_ver_doc


def register_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"email", "password", "full_name", "country", "currency", "language"})
    if unknown:
        return unknown
    message = "Too many registration attempts. Please try again later."
    limited = auth_ip_limit(
        operation="register", limit=REGISTER_LIMIT_PER_HOUR_PER_IP, message=message,
    )
    if limited:
        return limited
    values, err = validate_registration_inputs(kwargs.get("email"), kwargs.get("password"), kwargs.get("full_name"))
    if err:
        return err
    bootstrap, bootstrap_err = optional_bootstrap_inputs(kwargs)
    if bootstrap_err:
        return bootstrap_err
    email, password, full_name = values["email"], values["password"], values["full_name"]
    limited = auth_rate_limit(
        operation="register", dimension="identifier", value=email,
        limit=REGISTER_LIMIT_PER_HOUR_PER_EMAIL, message=message,
    )
    if limited:
        return limited

    existing = user_for_email(email)
    if existing:
        # Do not disclose account existence from the public registration boundary.
        # A retry against an address that already exists is intentionally accepted
        # without creating, mutating, or re-sending anything.
        return ok("If this email can be registered, a verification code has been queued.")

    try:
        user = frappe.new_doc("User")
        user.email = email
        user.first_name = full_name
        user.enabled = 0
        user.user_type = "Website User"
        user.send_welcome_email = 0
        user.new_password = password
        user.flags.no_welcome_mail = True
        mark_aos_managed_website_user_creation(user)
        user.insert(ignore_permissions=True)

        _pref, pref_err = create_auth_bootstrap(user.name, **bootstrap)
        if pref_err:
            frappe.db.rollback()
            return pref_err

        ver = ensure_ver_doc(user.name, purpose=EMAIL_VERIFICATION_PURPOSE)
        issue_otp(ver, email=email, full_name=full_name, purpose=EMAIL_VERIFICATION_PURPOSE)
        return ok("If this email can be registered, a verification code has been queued.")
    except frappe.DuplicateEntryError:
        frappe.db.rollback()
        # A concurrent duplicate insert is indistinguishable from a safe retry.
        return ok("If this email can be registered, a verification code has been queued.")
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Register Failed", exc, operation="register")
        safe_log_auth_event("AOS Register Failed", identifier=email, reason="unexpected")
        return fail("Registration failed. Please try again.", error="REGISTER_FAILED")
