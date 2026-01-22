import secrets

import frappe
from frappe.utils import add_to_date, now_datetime
from frappe.utils.data import sha256_hash


# Defaults (can be moved to a Settings DocType later)
OTP_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60


def generate_otp() -> str:
    """Generate a 6-digit OTP using a cryptographically secure RNG."""
    return f"{secrets.randbelow(900000) + 100000}"


def otp_hash(otp: str) -> str:
    return sha256_hash(otp)


def compute_expiry(minutes: int = OTP_TTL_MINUTES):
    return add_to_date(now_datetime(), minutes=minutes)


def get_ver_doc(user_name: str):
    name = frappe.db.get_value("AOS Email Verification", {"user": user_name}, "name")
    if not name:
        return None
    return frappe.get_doc("AOS Email Verification", name)


def send_otp_email(email: str, otp: str, full_name: str = ""):
    subject = "Your Africa Online Stores verification code"
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    message = f"""
        <p>{greeting}</p>
        <p>Your verification code is:</p>
        <h2 style=\"letter-spacing:2px\">{otp}</h2>
        <p>This code expires in {OTP_TTL_MINUTES} minutes.</p>
    """
    # Keep now=True for simplicity; consider enqueueing for better API latency.
    frappe.sendmail(recipients=[email], subject=subject, message=message, now=True)
