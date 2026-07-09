"""Account registration implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.account_status import get_account_state
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok, fail

from .account_helpers import ensure_aos_profile, ensure_user_preference, safe_log_auth_event
from .constants import REGISTER_LIMIT_PER_HOUR_PER_IP
from .validators import optional_bootstrap_inputs, validate_registration_inputs
from .verification import (
    compute_expiry,
    ensure_ver_doc,
    generate_otp,
    otp_hash,
    send_otp_email,
)


def register_impl(**kwargs):
    # Rate limit by IP. Request body fields are not part of this key.
    rl = rate_limit(
        key=rate_limit_key("auth", "register", "ip", request_ip()),
        ttl_seconds=60 * 60,
        limit=REGISTER_LIMIT_PER_HOUR_PER_IP,
        message="Too many registration attempts. Please try again later.",
    )
    if rl:
        safe_log_auth_event("AOS Register Rate Limited", reason="rate_limit")
        return rl

    values, err = validate_registration_inputs(
        kwargs.get("email"),
        kwargs.get("password"),
        kwargs.get("full_name"),
    )
    if err:
        safe_log_auth_event("AOS Register Malformed", reason="validation")
        return err

    bootstrap_inputs, bootstrap_err = optional_bootstrap_inputs(kwargs)
    if bootstrap_err:
        safe_log_auth_event("AOS Register Malformed", identifier=values["email"], reason="bootstrap_validation")
        return bootstrap_err

    email = values["email"]
    full_name = values["full_name"]
    password = values["password"]

    existing_user = frappe.db.get_value("User", {"email": email}, "name")
    if existing_user:
        state = get_account_state(existing_user)
        if state.get("is_deleted"):
            return fail(
                "This account was previously deleted. Please restore it instead.",
                error="ACCOUNT_DELETED_RESTORABLE",
                data={"can_restore": bool(state.get("can_restore"))},
            )

        return fail("An account with this email already exists.", error="ALREADY_EXISTS")

    try:
        # Create disabled user. ``ignore_permissions=True`` is required because
        # guest signup must be able to create exactly its own Website User.
        user = frappe.new_doc("User")
        user.email = email
        user.first_name = full_name
        user.enabled = 0
        user.user_type = "Website User"
        user.send_welcome_email = 0
        user.new_password = password
        user.flags.ignore_password_policy = True
        user.flags.no_welcome_mail = True
        user.insert(ignore_permissions=True)

        ensure_aos_profile(user.name)
        pref, pref_err = ensure_user_preference(user.name, **bootstrap_inputs)
        if pref_err:
            frappe.db.rollback()
            return pref_err

        otp = generate_otp()
        ver = ensure_ver_doc(user.name, email=email, purpose="email_verification")
        ver.otp_hash = otp_hash(otp)
        ver.expires_at = compute_expiry()
        ver.is_used = 0
        ver.attempts = 0
        ver.last_sent_at = frappe.utils.now_datetime()
        ver.reset_token_hash = ""
        ver.reset_token_expires_at = None
        ver.save(ignore_permissions=True)

        # Commit before sending so the emailed OTP always matches durable state.
        frappe.db.commit()

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Register Failed")
        frappe.db.rollback()
        return fail("Registration failed. Please try again.", error="REGISTER_FAILED")

    try:
        send_otp_email(email=email, otp=otp, full_name=full_name, purpose="email_verification")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Register OTP Email Failed")
        return fail(
            "Account created, but verification email could not be sent. Please request a new OTP.",
            error="SERVICE_UNAVAILABLE",
        )

    return ok("OTP sent to email. Please verify to activate account.")
