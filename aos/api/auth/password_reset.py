import frappe
from frappe.utils import now_datetime

from aos.api.shared.account_status import (
    can_restore_account,
    deleted_account_response,
    is_account_deleted,
)
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
GENERIC_REQUEST_MESSAGE = "If an account exists for this email, an OTP has been sent."


def _deleted_account_block(user_name: str):
    if is_account_deleted(user_name):
        return deleted_account_response(
            restorable=can_restore_account(user_name),
        )

    return None


def forgot_password_request_impl(**kwargs):
    email = normalize_email(kwargs.get("email") or "")

    if not email:
        return fail("Email is required.", code="VALIDATION_ERROR")

    # Rate limit by email
    rl = rate_limit(
        key=f"aos:fp:req:email:{email}",
        ttl_seconds=60 * 60,
        limit=FORGOT_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many requests. Please try again later.",
    )
    if rl:
        return rl

    # Rate limit by IP
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
        # Do not leak account existence
        return ok(GENERIC_REQUEST_MESSAGE)

    if is_account_deleted(user_name):
        # Do not send password-reset OTPs for deleted accounts.
        # The user must use the restore-account flow instead.
        # Keep this response generic to avoid account-state enumeration.
        return ok(GENERIC_REQUEST_MESSAGE)

    user = frappe.get_doc("User", user_name)

    ver = ensure_ver_doc(user_name, email=email, purpose=PURPOSE)

    cooldown = enforce_resend_cooldown(ver)
    if cooldown:
        return cooldown

    # Issue fresh OTP and clear reset token
    issue_otp(
        ver,
        email=email,
        full_name=user.first_name or "",
        purpose=PURPOSE,
    )

    ver.reset_token_hash = ""
    ver.reset_token_expires_at = None
    ver.save(ignore_permissions=True)

    return ok(GENERIC_REQUEST_MESSAGE)


def forgot_password_verify_otp_impl(**kwargs):
    email = normalize_email(kwargs.get("email") or "")
    otp = (kwargs.get("otp") or "").strip()

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

    deleted_err = _deleted_account_block(user_name)
    if deleted_err:
        return deleted_err

    ver = get_ver_doc(user_name, purpose=PURPOSE)

    if not ver:
        return fail("OTP not found. Please request a new OTP.", code="OTP_NOT_FOUND")

    err = verify_otp(ver, otp, consume=True)

    if err:
        return err

    # Issue one-time reset token
    token = generate_reset_token()

    ver.reset_token_hash = otp_hash(token)
    ver.reset_token_expires_at = compute_reset_token_expiry()
    ver.save(ignore_permissions=True)

    return ok(
        "OTP verified.",
        data={"reset_token": token},
    )


def forgot_password_reset_impl(**kwargs):
    email = normalize_email(kwargs.get("email") or "")
    reset_token = (kwargs.get("reset_token") or "").strip()
    new_password = kwargs.get("new_password") or ""
    confirm_password = kwargs.get("confirm_password") or ""

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
        return fail(
            "New password and confirm password are required.",
            code="VALIDATION_ERROR",
        )

    if new_password != confirm_password:
        return fail("Passwords do not match.", code="PASSWORD_MISMATCH")

    pw_err = validate_password_strength(new_password)

    if pw_err:
        return pw_err

    user_name = frappe.db.get_value("User", {"email": email}, "name")

    if not user_name:
        return fail("Invalid reset token.", code="TOKEN_INVALID")

    deleted_err = _deleted_account_block(user_name)
    if deleted_err:
        return deleted_err

    ver = get_ver_doc(user_name, purpose=PURPOSE)

    if not ver or not getattr(ver, "reset_token_hash", None):
        return fail("Invalid reset token.", code="TOKEN_INVALID")

    if not ver.reset_token_expires_at or now_datetime() > ver.reset_token_expires_at:
        return fail(
            "Reset token expired. Please request a new OTP.",
            code="TOKEN_EXPIRED",
        )

    if otp_hash(reset_token) != ver.reset_token_hash:
        return fail("Invalid reset token.", code="TOKEN_INVALID")

    # Update password
    user = frappe.get_doc("User", user_name)

    user.new_password = new_password
    user.flags.ignore_password_policy = True
    user.save(ignore_permissions=True)

    # Clear token
    ver.reset_token_hash = ""
    ver.reset_token_expires_at = None
    ver.save(ignore_permissions=True)

    return ok("Password updated successfully.")
