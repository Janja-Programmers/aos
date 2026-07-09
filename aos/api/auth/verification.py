import secrets

import frappe
from frappe.utils import add_to_date, now_datetime
from frappe.utils.data import sha256_hash


# Defaults (can be moved to a Settings DocType later)
OTP_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
RESET_TOKEN_TTL_MINUTES = 15  # token issued after OTP verification for password reset


EMAIL_VERIFICATION_PURPOSE = "email_verification"
PASSWORD_RESET_PURPOSE = "password_reset"
ACCOUNT_RESTORE_PURPOSE = "account_restore"


def generate_otp() -> str:
    """Generate a 6-digit OTP using a cryptographically secure RNG."""
    return f"{secrets.randbelow(900000) + 100000}"


def otp_hash(value: str) -> str:
    return sha256_hash(value or "")


def compute_expiry(minutes: int = OTP_TTL_MINUTES):
    return add_to_date(now_datetime(), minutes=minutes)


def compute_reset_token_expiry(minutes: int = RESET_TOKEN_TTL_MINUTES):
    return add_to_date(now_datetime(), minutes=minutes)


def generate_reset_token() -> str:
    """Generate a one-time token for completing password reset."""
    return secrets.token_urlsafe(32)


def get_ver_doc(user_name: str, purpose: str = EMAIL_VERIFICATION_PURPOSE):
    """Fetch OTP doc for a user + purpose."""
    name = frappe.db.get_value(
        "AOS Email Verification",
        {"user": user_name, "purpose": purpose},
        "name",
    )
    if not name:
        return None
    return frappe.get_doc("AOS Email Verification", name)


def ensure_ver_doc(user_name: str, email: str, purpose: str):
    """Get or create OTP doc for a user + purpose.

    The DocType autoname is user-purpose, so concurrent duplicate creation is
    safely collapsed to the existing row.
    """
    ver = get_ver_doc(user_name, purpose=purpose)
    if ver:
        return ver

    doc = frappe.get_doc(
        {
            "doctype": "AOS Email Verification",
            "user": user_name,
            "purpose": purpose,
            "email": email,
            "otp_hash": "",
            "expires_at": None,
            "is_used": 0,
            "attempts": 0,
            "last_sent_at": None,
            "reset_token_hash": "",
            "reset_token_expires_at": None,
        }
    )

    try:
        doc.insert(ignore_permissions=True)
        return doc
    except frappe.DuplicateEntryError:
        existing = get_ver_doc(user_name, purpose=purpose)
        if existing:
            return existing
        raise


def has_pending_email_verification(user_name: str) -> bool:
    """Return whether a disabled user is specifically pending email verification."""
    ver = get_ver_doc(user_name, purpose=EMAIL_VERIFICATION_PURPOSE)
    if not ver:
        return False
    return int(getattr(ver, "is_used", 0) or 0) == 0


def send_otp_email(email: str, otp: str, full_name: str = "", purpose: str = EMAIL_VERIFICATION_PURPOSE):
    if purpose == PASSWORD_RESET_PURPOSE:
        subject = "Your Africa Online Stores password reset code"
        action = "reset your password"
    elif purpose == ACCOUNT_RESTORE_PURPOSE:
        subject = "Your Africa Online Stores account restore code"
        action = "restore your account"
    else:
        subject = "Your Africa Online Stores verification code"
        action = "verify your email"

    greeting = f"Hi {full_name}," if full_name else "Hi,"
    message = f"""
        <p>{greeting}</p>
        <p>Your code to {action} is:</p>
        <h2 style=\"letter-spacing:2px\">{otp}</h2>
        <p>This code expires in {OTP_TTL_MINUTES} minutes.</p>
    """
    frappe.sendmail(recipients=[email], subject=subject, message=message, now=True)
