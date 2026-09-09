"""Verification credential primitives.

OTP values use Frappe's slow password hashing context. High-entropy reset tokens
use a one-way SHA-256 digest and constant-time comparison at verification time.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets

import frappe
from aos.aos.doctype.aos_auth_challenge.aos_auth_challenge import challenge_name
from frappe.utils import add_to_date, now_datetime
from frappe.utils.password import passlibctx

OTP_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
CONTINUATION_TOKEN_TTL_MINUTES = 15

EMAIL_VERIFICATION_PURPOSE = "email_verification"
PASSWORD_RESET_PURPOSE = "password_reset"
ACCOUNT_RESTORE_PURPOSE = "account_restore"
TWO_FACTOR_PURPOSE = "two_factor"


def generate_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000}"


def hash_otp(value: str) -> str:
    """Slow-hash a low-entropy OTP so a DB leak is not cheaply brute-forced."""
    return passlibctx.hash(value or "")


def verify_otp_password_hash(value: str, encoded: str) -> bool:
    if not value or not encoded:
        return False
    try:
        return bool(passlibctx.verify(value, encoded))
    except Exception:
        return False


def token_digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def token_matches(value: str, stored_digest: str) -> bool:
    if not value or not stored_digest:
        return False
    return hmac.compare_digest(token_digest(value), str(stored_digest))


def compute_expiry(minutes: int = OTP_TTL_MINUTES):
    return add_to_date(now_datetime(), minutes=minutes)


def compute_continuation_expiry(minutes: int = CONTINUATION_TOKEN_TTL_MINUTES):
    return add_to_date(now_datetime(), minutes=minutes)


def generate_continuation_token() -> str:
    return secrets.token_urlsafe(32)



def get_ver_doc(user_name: str, purpose: str = EMAIL_VERIFICATION_PURPOSE, *, for_update: bool = False):
    """Fetch the one deterministic verification row for user + purpose."""
    name = challenge_name(user_name, purpose)
    if for_update:
        rows = frappe.db.sql(
            "SELECT name FROM `tabAOS Auth Challenge` WHERE name = %s LIMIT 1 FOR UPDATE",
            (name,),
        )
        if not rows:
            return None
        return frappe.get_doc("AOS Auth Challenge", name)
    if not frappe.db.exists("AOS Auth Challenge", name):
        return None
    return frappe.get_doc("AOS Auth Challenge", name)


def ensure_ver_doc(
    user_name: str,
    *,
    purpose: str = EMAIL_VERIFICATION_PURPOSE,
    for_update: bool = False,
):
    existing = get_ver_doc(user_name, purpose=purpose, for_update=for_update)
    if existing:
        return existing
    doc = frappe.new_doc("AOS Auth Challenge")
    doc.user = user_name
    doc.purpose = purpose
    try:
        doc.insert(ignore_permissions=True)
        return doc
    except frappe.DuplicateEntryError:
        return get_ver_doc(user_name, purpose=purpose, for_update=for_update)


def has_pending_email_verification(user_name: str) -> bool:
    ver = get_ver_doc(user_name, EMAIL_VERIFICATION_PURPOSE)
    return bool(ver and int(getattr(ver, "is_used", 0) or 0) == 0)


def queue_otp_email(*, email: str, otp: str, full_name: str = "", purpose: str) -> None:
    """Insert email into Frappe Email Queue as part of the surrounding transaction."""
    if purpose == EMAIL_VERIFICATION_PURPOSE:
        subject = "Verify your AOS account"
        intro = "Use this code to verify your AOS account:"
    elif purpose == PASSWORD_RESET_PURPOSE:
        subject = "Reset your AOS password"
        intro = "Use this code to continue your AOS password reset:"
    elif purpose == TWO_FACTOR_PURPOSE:
        subject = "Your AOS login verification code"
        intro = "Use this code to finish signing in to your AOS account:"
    else:
        subject = "Restore your AOS account"
        intro = "Use this code to restore your AOS account:"
    greeting = f"Hello {html.escape(full_name)},<br><br>" if full_name else ""
    message = (
        f"{greeting}{intro}<br><br>"
        f"<strong style='font-size: 24px; letter-spacing: 4px'>{html.escape(otp)}</strong>"
        f"<br><br>This code expires in {OTP_TTL_MINUTES} minutes. If you did not request it, ignore this email."
    )
    queued = frappe.sendmail(
        recipients=[email],
        subject=subject,
        message=message,
        now=False,
        redact_message_after_send=True,
    )
    if queued and getattr(queued, "name", None):
        from .email_delivery import schedule_auth_email_delivery

        schedule_auth_email_delivery(queued.name)
