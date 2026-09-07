"""OTP issuance and verification domain logic."""

from __future__ import annotations

from frappe.utils import now_datetime

from aos.api.shared.responses import fail

from .verification import (
    MAX_ATTEMPTS,
    RESEND_COOLDOWN_SECONDS,
    compute_expiry,
    generate_otp,
    hash_otp,
    queue_otp_email,
    verify_otp_password_hash,
)

PUBLIC_OTP_INVALID_MESSAGE = "Invalid or expired OTP."
_DUMMY_OTP_HASH = hash_otp("000000")


def public_otp_invalid():
    return fail(PUBLIC_OTP_INVALID_MESSAGE, error="OTP_INVALID")


def resend_allowed(ver) -> bool:
    """Internal resend cooldown. Public endpoints intentionally do not reveal it."""
    last_sent = getattr(ver, "last_sent_at", None)
    if not last_sent:
        return True
    return (now_datetime() - last_sent).total_seconds() >= RESEND_COOLDOWN_SECONDS


def persist_otp(ver) -> str:
    otp = generate_otp()
    ver.otp_password_hash = hash_otp(otp)
    ver.expires_at = compute_expiry()
    ver.is_used = 0
    ver.attempts = 0
    ver.last_sent_at = now_datetime()
    ver.reset_token_hash = ""
    ver.reset_token_expires_at = None
    ver.save(ignore_permissions=True)
    return otp


def issue_otp(ver, *, email: str, full_name: str = "", purpose: str):
    """Persist OTP state and durable Email Queue state in the same transaction."""
    otp = persist_otp(ver)
    queue_otp_email(email=email, otp=otp, full_name=full_name or "", purpose=purpose)
    return otp


def _dummy_otp_work(otp: str) -> None:
    try:
        verify_otp_password_hash(otp, _DUMMY_OTP_HASH)
    except Exception:
        pass


def verify_otp(ver, otp: str, *, consume: bool = False):
    otp = otp.strip() if isinstance(otp, str) else ""
    if not ver:
        _dummy_otp_work(otp)
        return fail("OTP not found. Please request a new OTP.", error="OTP_NOT_FOUND")
    if int(getattr(ver, "is_used", 0) or 0) == 1:
        _dummy_otp_work(otp)
        return fail("OTP already used. Please request a new OTP.", error="OTP_USED")
    if not getattr(ver, "expires_at", None) or now_datetime() > ver.expires_at:
        _dummy_otp_work(otp)
        return fail("OTP expired. Please request a new OTP.", error="OTP_EXPIRED")
    if int(getattr(ver, "attempts", 0) or 0) >= MAX_ATTEMPTS:
        _dummy_otp_work(otp)
        return fail("Too many attempts. Please request a new OTP.", error="OTP_MAX_ATTEMPTS")
    if not verify_otp_password_hash(otp, str(getattr(ver, "otp_password_hash", "") or "")):
        ver.attempts = int(getattr(ver, "attempts", 0) or 0) + 1
        ver.save(ignore_permissions=True)
        return fail("Invalid OTP.", error="OTP_INVALID")
    if consume:
        ver.is_used = 1
        ver.otp_password_hash = ""
        ver.save(ignore_permissions=True)
    return None


def verify_public_otp(ver, otp: str, *, consume: bool = False):
    """Collapse all verification-state failures to one enumeration-safe contract."""
    err = verify_otp(ver, otp, consume=consume)
    return public_otp_invalid() if err else None
