import frappe

from aos.api.shared.responses import ok, fail
from aos.api.shared.rate_limit import rate_limit 
from .constants import RESEND_LIMIT_PER_HOUR_PER_EMAIL, VERIFY_LIMIT_PER_HOUR_PER_EMAIL
from .validators import normalize_email
from .verification import get_ver_doc
from .otp_service import enforce_resend_cooldown, issue_otp, verify_otp


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

    ver = get_ver_doc(user_name, purpose="email_verification")
    if not ver:
        return fail("OTP not found. Please request a new OTP.", code="OTP_NOT_FOUND")

    err = verify_otp(ver, otp, consume=True)
    if err:
        return err

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

    ver = get_ver_doc(user_name, purpose="email_verification")
    if not ver:
        return fail("OTP record not found. Please register again.", code="OTP_RECORD_MISSING")

    cooldown = enforce_resend_cooldown(ver)
    if cooldown:
        return cooldown

    issue_otp(
        ver,
        email=email,
        full_name=user.first_name or "",
        purpose="email_verification",
    )

    return ok("New OTP sent to email.")
