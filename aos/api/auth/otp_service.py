import frappe
from frappe.utils import now_datetime

from .responses import fail
from .verification import MAX_ATTEMPTS, RESEND_COOLDOWN_SECONDS, otp_hash, compute_expiry, generate_otp, send_otp_email


def enforce_resend_cooldown(ver):
    """Return a fail() response if still in cooldown, else None."""
    if getattr(ver, "last_sent_at", None):
        delta = (now_datetime() - ver.last_sent_at).total_seconds()
        if delta < RESEND_COOLDOWN_SECONDS:
            wait = int(RESEND_COOLDOWN_SECONDS - delta)
            return fail(
                f"Please wait {wait}s before requesting another OTP.",
                code="COOLDOWN",
                data={"wait_seconds": wait},
            )
    return None


def issue_otp(ver, *, email: str, full_name: str = "", purpose: str):
    """Generate a new OTP, persist it on the verification doc and send it."""
    otp = generate_otp()
    ver.otp_hash = otp_hash(otp)
    ver.expires_at = compute_expiry()
    ver.is_used = 0
    ver.attempts = 0
    ver.last_sent_at = now_datetime()
    ver.save(ignore_permissions=True)

    send_otp_email(email=email, otp=otp, full_name=full_name or "", purpose=purpose)
    return otp


def verify_otp(ver, otp: str, *, consume: bool = False):
    """Validate OTP for a given verification doc.

    Returns a fail() response on error, otherwise None.
    If consume=True, marks the OTP as used on success.
    """
    otp = (otp or "").strip()

    if int(ver.is_used or 0) == 1:
        return fail("OTP already used. Please request a new OTP.", code="OTP_USED")

    if not getattr(ver, "expires_at", None) or now_datetime() > ver.expires_at:
        return fail("OTP expired. Please request a new OTP.", code="OTP_EXPIRED")

    if int(ver.attempts or 0) >= MAX_ATTEMPTS:
        return fail("Too many attempts. Please request a new OTP.", code="OTP_MAX_ATTEMPTS")

    if otp_hash(otp) != ver.otp_hash:
        ver.attempts = int(ver.attempts or 0) + 1
        ver.save(ignore_permissions=True)
        return fail("Invalid OTP.", code="OTP_INVALID")

    if consume:
        ver.is_used = 1
        ver.save(ignore_permissions=True)

    return None
