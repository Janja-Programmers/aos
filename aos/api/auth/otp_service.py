import frappe
from frappe.utils import now_datetime

from aos.api.shared.responses import fail
from .verification import MAX_ATTEMPTS, RESEND_COOLDOWN_SECONDS, otp_hash, compute_expiry, generate_otp, send_otp_email


PUBLIC_OTP_INVALID_MESSAGE = "Invalid or expired OTP."


def public_otp_invalid():
    return fail(PUBLIC_OTP_INVALID_MESSAGE, error="OTP_INVALID")


def enforce_resend_cooldown(ver):
    """Return a fail() response if still in cooldown, else None."""
    if getattr(ver, "last_sent_at", None):
        delta = (now_datetime() - ver.last_sent_at).total_seconds()
        if delta < RESEND_COOLDOWN_SECONDS:
            wait = int(RESEND_COOLDOWN_SECONDS - delta)
            return fail(
                f"Please wait {wait}s before requesting another OTP.",
                error="COOLDOWN",
                data={"wait_seconds": wait},
            )
    return None


def persist_otp(ver) -> str:
    """Generate and persist a fresh OTP without sending email."""
    otp = generate_otp()
    ver.otp_hash = otp_hash(otp)
    ver.expires_at = compute_expiry()
    ver.is_used = 0
    ver.attempts = 0
    ver.last_sent_at = now_datetime()
    ver.save(ignore_permissions=True)
    return otp


def issue_otp(ver, *, email: str, full_name: str = "", purpose: str, commit_before_send: bool = False):
    """Generate a new OTP, persist it, optionally commit, then send it.

    ``commit_before_send`` is used by public auth flows so an emailed OTP always
    corresponds to durable DB state. If email delivery fails after commit, the
    user can safely request another OTP.
    """
    otp = persist_otp(ver)
    if commit_before_send:
        frappe.db.commit()

    send_otp_email(email=email, otp=otp, full_name=full_name or "", purpose=purpose)
    return otp


def verify_otp(ver, otp: str, *, consume: bool = False):
    """Validate OTP for a given verification doc.

    Returns a fail() response on error, otherwise None.
    If consume=True, marks the OTP as used on success.
    """
    otp = otp.strip() if isinstance(otp, str) else ""

    if int(ver.is_used or 0) == 1:
        return fail("OTP already used. Please request a new OTP.", error="OTP_USED")

    if not getattr(ver, "expires_at", None) or now_datetime() > ver.expires_at:
        return fail("OTP expired. Please request a new OTP.", error="OTP_EXPIRED")

    if int(ver.attempts or 0) >= MAX_ATTEMPTS:
        return fail("Too many attempts. Please request a new OTP.", error="OTP_MAX_ATTEMPTS")

    if otp_hash(otp) != ver.otp_hash:
        ver.attempts = int(ver.attempts or 0) + 1
        ver.save(ignore_permissions=True)
        return fail("Invalid OTP.", error="OTP_INVALID")

    if consume:
        ver.is_used = 1
        ver.save(ignore_permissions=True)

    return None


def verify_public_otp(ver, otp: str, *, consume: bool = False):
    """Verify public unauthenticated OTP without leaking OTP record state."""
    if not ver:
        return public_otp_invalid()

    err = verify_otp(ver, otp, consume=consume)
    if err:
        return public_otp_invalid()

    return None
