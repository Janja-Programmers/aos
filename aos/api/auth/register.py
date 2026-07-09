"""Account registration implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.account_status import get_account_state
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok, fail

from .account_helpers import ensure_aos_profile, ensure_user_preference, safe_log_auth_event
from .constants import REGISTER_LIMIT_PER_HOUR_PER_IP
from .validators import normalize_email, normalize_name, validate_registration_inputs
from .verification import (
    compute_expiry,
    ensure_ver_doc,
    generate_otp,
    otp_hash,
    send_otp_email,
)


def register_impl(**kwargs):
    email = normalize_email(kwargs.get("email") or "")
    full_name = normalize_name(kwargs.get("full_name") or "")
    password = kwargs.get("password") if isinstance(kwargs.get("password"), str) else ""

    # Rate limit by IP. Request body fields are not part of this key.
    rl = rate_limit(
        key=rate_limit_key("auth", "register", "ip", request_ip()),
        ttl_seconds=60 * 60,
        limit=REGISTER_LIMIT_PER_HOUR_PER_IP,
        message="Too many registration attempts. Please try again later.",
    )
    if rl:
        safe_log_auth_event("AOS Register Rate Limited", identifier=email, reason="rate_limit")
        return rl

    err = validate_registration_inputs(email, password, full_name)
    if err:
        safe_log_auth_event("AOS Register Malformed", identifier=email, reason="validation")
        return err

    # Prevent duplicate accounts.
    # If the old account is deleted, do not create a new User with the same email.
    # The same User must be restored instead to keep old AOS links safe.
    existing_user = frappe.db.get_value("User", {"email": email}, "name")
    if existing_user:
        state = get_account_state(existing_user)
        if state.get("is_deleted"):
            return fail(
                "This account was previously deleted. Please restore it instead.",
                error="ACCOUNT_DELETED_RESTORABLE",
                data={"can_restore": bool(state.get("can_restore"))},
                http_status=403,
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

        # Set password during insert, not after insert.
        # This prevents Frappe from sending a "password changed" email on signup.
        user.new_password = password
        user.flags.ignore_password_policy = True
        user.flags.no_welcome_mail = True
        user.insert(ignore_permissions=True)

        ensure_aos_profile(user.name)
        pref, pref_err = ensure_user_preference(
            user.name,
            country=kwargs.get("country"),
            language=kwargs.get("language"),
            currency=kwargs.get("currency"),
        )
        if pref_err:
            frappe.db.rollback()
            return pref_err

        otp = generate_otp()
        expires_at = compute_expiry()

        ver = ensure_ver_doc(user.name, email=email, purpose="email_verification")
        ver.otp_hash = otp_hash(otp)
        ver.expires_at = expires_at
        ver.is_used = 0
        ver.attempts = 0
        ver.last_sent_at = frappe.utils.now_datetime()
        ver.reset_token_hash = ""
        ver.reset_token_expires_at = None
        ver.save(ignore_permissions=True)

        send_otp_email(
            email=email,
            otp=otp,
            full_name=full_name,
            purpose="email_verification",
        )

        frappe.db.commit()
        return ok("OTP sent to email. Please verify to activate account.")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Register Failed")
        frappe.db.rollback()
        return fail("Registration failed. Please try again.", error="REGISTER_FAILED")
