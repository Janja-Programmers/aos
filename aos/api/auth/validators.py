import re

from .responses import fail


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def normalize_name(full_name: str) -> str:
    return (full_name or "").strip()


def validate_registration_inputs(email: str, password: str, full_name: str):
    if not full_name or len(full_name.strip()) < 2:
        return fail("Full name is required.", code="VALIDATION_ERROR")
    if not email or not EMAIL_REGEX.match(email.strip().lower()):
        return fail("A valid email is required.", code="VALIDATION_ERROR")
    if not password or len(password) < 8:
        return fail("Password must be at least 8 characters long.", code="VALIDATION_ERROR")
    return None


def validate_email(email: str):
    if not email or not EMAIL_REGEX.match(email):
        return fail("A valid email is required.", code="VALIDATION_ERROR")
    return None
