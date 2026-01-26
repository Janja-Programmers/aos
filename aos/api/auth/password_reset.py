import frappe
from frappe.utils import now_datetime

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from .constants import (
    FORGOT_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
    FORGOT_VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
    FORGOT_RESET_LIMIT_PER_HOUR_PER_EMAIL,
)
from .validators import normalize_email, validate_password_strength
from .verification import (
    compute_reset_token_expiry,
    ensure_ver_doc,
    generate_reset_token,
    get_ver_doc,
    otp_hash,
)
from .otp_service import enforce_resend_cooldown, issue_otp, verify_otp


PURPOSE = "password_reset"


def forgot_password_request_impl(email: str):
    email = normalize_email(email)
    if not email:
        return fail("Email is required.", code="VALIDATION_ERROR")

    # Rate limit by email + IP
    rl = rate_limit(
        key=f"aos:fp:req:email:{email}",
        ttl_seconds=60 * 60,
        limit=FORGOT_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many requests. Please try again later.",
    )
    if rl:
        return rl

    rl2 = rate_limit(
        key=f"aos:fp:req:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=FORGOT_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many requests. Please try again later.",
    )
    if rl2:
        return rl2

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        # Don't leak whether the account exists
        return ok("If an account exists for this email, an OTP has been sent.")

    user = frappe.get_doc("User", user_name)

    ver = ensure_ver_doc(user_name, email=email, purpose=PURPOSE)

    cooldown = enforce_resend_cooldown(ver)
    if cooldown:
        return cooldown

    # issue a fresh OTP and clear any previous reset token
    issue_otp(
        ver,
        email=email,
        full_name=user.first_name or "",
        purpose=PURPOSE,
    )
    ver.reset_token_hash = ""
    ver.reset_token_expires_at = None
    ver.save(ignore_permissions=True)

    return ok("If an account exists for this email, an OTP has been sent.")


def forgot_password_verify_otp_impl(email: str, otp: str):
    email = normalize_email(email)
    otp = (otp or "").strip()

    rl = rate_limit(
        key=f"aos:fp:verify:email:{email}",
        ttl_seconds=60 * 60,
        limit=FORGOT_VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many attempts. Please try again later.",
    )
    if rl:
        return rl

    if not email or not otp:
        return fail("Email and OTP are required.", code="VALIDATION_ERROR")

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        return fail("Invalid OTP.", code="OTP_INVALID")

    ver = get_ver_doc(user_name, purpose=PURPOSE)
    if not ver:
        return fail("OTP not found. Please request a new OTP.", code="OTP_NOT_FOUND")

    err = verify_otp(ver, otp, consume=True)
    if err:
        return err

    # Issue reset token (one-time)
    token = generate_reset_token()
    ver.reset_token_hash = otp_hash(token)
    ver.reset_token_expires_at = compute_reset_token_expiry()
    ver.save(ignore_permissions=True)

    return ok("OTP verified.", data={"reset_token": token})


def forgot_password_reset_impl(email: str, reset_token: str, new_password: str, confirm_password: str):
    email = normalize_email(email)
    reset_token = (reset_token or "").strip()
    new_password = new_password or ""
    confirm_password = confirm_password or ""

    rl = rate_limit(
        key=f"aos:fp:reset:email:{email}",
        ttl_seconds=60 * 60,
        limit=FORGOT_RESET_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many attempts. Please try again later.",
    )
    if rl:
        return rl

    if not email or not reset_token:
        return fail("Email and reset token are required.", code="VALIDATION_ERROR")

    if not new_password or not confirm_password:
        return fail("New password and confirm password are required.", code="VALIDATION_ERROR")

    if new_password != confirm_password:
        return fail("Passwords do not match.", code="PASSWORD_MISMATCH")

    pw_err = validate_password_strength(new_password)
    if pw_err:
        return pw_err

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        return fail("Invalid reset token.", code="TOKEN_INVALID")

    ver = get_ver_doc(user_name, purpose=PURPOSE)
    if not ver or not getattr(ver, "reset_token_hash", None):
        return fail("Invalid reset token.", code="TOKEN_INVALID")

    if not ver.reset_token_expires_at or now_datetime() > ver.reset_token_expires_at:
        return fail("Reset token expired. Please request a new OTP.", code="TOKEN_EXPIRED")

    if otp_hash(reset_token) != ver.reset_token_hash:
        return fail("Invalid reset token.", code="TOKEN_INVALID")

    # Update password
    user = frappe.get_doc("User", user_name)
    user.new_password = new_password
    user.flags.ignore_password_policy = True  # adjust if enforcing password policy
    user.save(ignore_permissions=True)

    # Clear token
    ver.reset_token_hash = ""
    ver.reset_token_expires_at = None
    ver.save(ignore_permissions=True)

    return ok("Password updated successfully.")
