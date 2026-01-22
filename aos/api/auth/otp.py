import frappe
from frappe.utils import now_datetime

from .constants import RESEND_LIMIT_PER_HOUR_PER_EMAIL, VERIFY_LIMIT_PER_HOUR_PER_EMAIL
from .rate_limit import rate_limit
from .responses import fail, ok
from .validators import normalize_email
from .verification import (
    MAX_ATTEMPTS,
    RESEND_COOLDOWN_SECONDS,
    compute_expiry,
    generate_otp,
    get_ver_doc,
    otp_hash,
    send_otp_email,
)


def verify_email_otp_impl(email: str, otp: str):
    email = normalize_email(email)
    otp = (otp or "").strip()

    # rate limit per email
    rl = rate_limit(
        key=f"aos:verify:email:{email}",
        ttl_seconds=60 * 60,
        limit=VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many verification attempts. Please try again later.",
    )
    if rl:
        return rl

    if not email or not otp:
        return fail("Email and OTP are required.", code="VALIDATION_ERROR")

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        return fail("Account not found.", code="NOT_FOUND")

    ver = get_ver_doc(user_name)
    if not ver:
        return fail("OTP not found. Please request a new OTP.", code="OTP_NOT_FOUND")

    if int(ver.is_used or 0) == 1:
        return fail("OTP already used. Please request a new OTP.", code="OTP_USED")

    if now_datetime() > ver.expires_at:
        return fail("OTP expired. Please request a new OTP.", code="OTP_EXPIRED")

    if int(ver.attempts or 0) >= MAX_ATTEMPTS:
        return fail("Too many attempts. Please request a new OTP.", code="OTP_MAX_ATTEMPTS")

    if otp_hash(otp) != ver.otp_hash:
        ver.attempts = int(ver.attempts or 0) + 1
        ver.save(ignore_permissions=True)
        return fail("Invalid OTP.", code="OTP_INVALID")

    # Mark OTP used
    ver.is_used = 1
    ver.save(ignore_permissions=True)

    # Enable user
    user = frappe.get_doc("User", user_name)
    user.enabled = 1
    user.save(ignore_permissions=True)

    return ok("Email verified. Account activated.")


def resend_email_otp_impl(email: str):
    email = normalize_email(email)
    if not email:
        return fail("Email is required.", code="VALIDATION_ERROR")

    # rate limit per email
    rl = rate_limit(
        key=f"aos:resend:email:{email}",
        ttl_seconds=60 * 60,
        limit=RESEND_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many resend requests. Please try again later.",
    )
    if rl:
        return rl

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        return fail("Account not found.", code="NOT_FOUND")

    user = frappe.get_doc("User", user_name)
    if int(user.enabled or 0) == 1:
        return ok("Account already active.")

    ver = get_ver_doc(user_name)
    if not ver:
        return fail("OTP record not found. Please register again.", code="OTP_RECORD_MISSING")

    # cooldown using last_sent_at
    if getattr(ver, "last_sent_at", None):
        delta = (now_datetime() - ver.last_sent_at).total_seconds()
        if delta < RESEND_COOLDOWN_SECONDS:
            wait = int(RESEND_COOLDOWN_SECONDS - delta)
            return fail(
                f"Please wait {wait}s before requesting another OTP.",
                code="COOLDOWN",
                data={"wait_seconds": wait},
            )

    otp = generate_otp()
    ver.otp_hash = otp_hash(otp)
    ver.expires_at = compute_expiry()
    ver.is_used = 0
    ver.attempts = 0
    ver.last_sent_at = now_datetime()
    ver.save(ignore_permissions=True)

    send_otp_email(email=email, otp=otp, full_name=user.first_name or "")

    return ok("New OTP sent to email.")
