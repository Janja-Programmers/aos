import re
import random
import frappe
from frappe.utils import now_datetime, add_to_date
from frappe.utils.data import sha256_hash

OTP_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
REGISTER_LIMIT_PER_HOUR_PER_IP = 10
RESEND_LIMIT_PER_HOUR_PER_EMAIL = 10
VERIFY_LIMIT_PER_HOUR_PER_EMAIL = 30

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _generate_otp() -> str:
    return f"{random.randint(100000, 999999)}"


def _send_otp_email(email: str, otp: str, full_name: str = ""):
    subject = "Your Africa Online Stores verification code"
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    message = f"""
        <p>{greeting}</p>
        <p>Your verification code is:</p>
        <h2 style="letter-spacing:2px">{otp}</h2>
        <p>This code expires in {OTP_TTL_MINUTES} minutes.</p>
    """
    frappe.sendmail(recipients=[email], subject=subject, message=message, now=True)


def _validate(email: str, password: str, full_name: str):
    if not full_name or len(full_name.strip()) < 2:
        return {"ok": False, "message": "Full name is required."}
    if not email or not EMAIL_REGEX.match(email.strip().lower()):
        return {"ok": False, "message": "A valid email is required."}
    if not password or len(password) < 8:
        return {"ok": False, "message": "Password must be at least 8 characters long."}
    return None


def _request_ip() -> str:
    try:
        return frappe.local.request_ip or "unknown"
    except Exception:
        return "unknown"


def _cache_incr(key: str, ttl_seconds: int) -> int:
    cache = frappe.cache()
    val = cache.get_value(key) or 0
    val = int(val) + 1
    cache.set_value(key, val, expires_in_sec=ttl_seconds)
    return val


def _rate_limit(key: str, ttl_seconds: int, limit: int, message: str):
    if _cache_incr(key, ttl_seconds) > limit:
        return {"ok": False, "message": message}
    return None


def _get_ver_doc(user_name: str):
    name = frappe.db.get_value("AOS Email Verification", {"user": user_name}, "name")
    if not name:
        return None
    return frappe.get_doc("AOS Email Verification", name)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def register(email: str, password: str, full_name: str):
    email = (email or "").strip().lower()
    full_name = (full_name or "").strip()
    password = password or ""

    # rate limit by IP
    rl = _rate_limit(
        key=f"aos:reg:ip:{_request_ip()}",
        ttl_seconds=60 * 60,
        limit=REGISTER_LIMIT_PER_HOUR_PER_IP,
        message="Too many registration attempts. Please try again later."
    )
    if rl:
        return rl

    err = _validate(email, password, full_name)
    if err:
        return err

    if frappe.db.exists("User", {"email": email}):
        return {"ok": False, "message": "An account with this email already exists."}

    try:
        # Create disabled user
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": full_name,
            "enabled": 0,
            "user_type": "Website User",
            "send_welcome_email": 0
        })
        user.insert(ignore_permissions=True)

        # Set password
        user.new_password = password
        user.flags.ignore_password_policy = True  # remove later if you want strong policy
        user.save(ignore_permissions=True)

        # OTP record (one per user)
        otp = _generate_otp()
        otp_hash = sha256_hash(otp)
        expires_at = add_to_date(now_datetime(), minutes=OTP_TTL_MINUTES)

        ver = _get_ver_doc(user.name)
        if ver:
            ver.otp_hash = otp_hash
            ver.expires_at = expires_at
            ver.is_used = 0
            ver.attempts = 0
            ver.last_sent_at = now_datetime()
            ver.save(ignore_permissions=True)
        else:
            doc = frappe.get_doc({
                "doctype": "AOS Email Verification",
                "user": user.name,
                "email": email,
                "otp_hash": otp_hash,
                "expires_at": expires_at,
                "is_used": 0,
                "attempts": 0,
                "last_sent_at": now_datetime()
            })
            doc.insert(ignore_permissions=True)

        _send_otp_email(email=email, otp=otp, full_name=full_name)

        return {"ok": True, "message": "OTP sent to email. Please verify to activate account."}

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Register Failed")
        # If user got created, keep it disabled
        try:
            if frappe.db.exists("User", {"email": email}):
                frappe.db.set_value("User", email, "enabled", 0)
        except Exception:
            pass
        return {"ok": False, "message": "Registration failed. Please try again."}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def verify_email_otp(email: str, otp: str):
    email = (email or "").strip().lower()
    otp = (otp or "").strip()

    # rate limit per email
    rl = _rate_limit(
        key=f"aos:verify:email:{email}",
        ttl_seconds=60 * 60,
        limit=VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many verification attempts. Please try again later."
    )
    if rl:
        return rl

    if not email or not otp:
        return {"ok": False, "message": "Email and OTP are required."}

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        return {"ok": False, "message": "Account not found."}

    ver = _get_ver_doc(user_name)
    if not ver:
        return {"ok": False, "message": "OTP not found. Please request a new OTP."}

    if int(ver.is_used or 0) == 1:
        return {"ok": False, "message": "OTP already used. Please request a new OTP."}

    if now_datetime() > ver.expires_at:
        return {"ok": False, "message": "OTP expired. Please request a new OTP."}

    if int(ver.attempts or 0) >= MAX_ATTEMPTS:
        return {"ok": False, "message": "Too many attempts. Please request a new OTP."}

    if sha256_hash(otp) != ver.otp_hash:
        ver.attempts = int(ver.attempts or 0) + 1
        ver.save(ignore_permissions=True)
        return {"ok": False, "message": "Invalid OTP."}

    # Mark OTP used
    ver.is_used = 1
    ver.save(ignore_permissions=True)

    # Enable user
    user = frappe.get_doc("User", user_name)
    user.enabled = 1
    user.save(ignore_permissions=True)

    return {"ok": True, "message": "Email verified. Account activated."}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def resend_email_otp(email: str):
    email = (email or "").strip().lower()
    if not email:
        return {"ok": False, "message": "Email is required."}

    # rate limit per email
    rl = _rate_limit(
        key=f"aos:resend:email:{email}",
        ttl_seconds=60 * 60,
        limit=RESEND_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many resend requests. Please try again later."
    )
    if rl:
        return rl

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        return {"ok": False, "message": "Account not found."}

    user = frappe.get_doc("User", user_name)
    if int(user.enabled or 0) == 1:
        return {"ok": True, "message": "Account already active."}

    ver = _get_ver_doc(user_name)
    if not ver:
        return {"ok": False, "message": "OTP record not found. Please register again."}

    # cooldown using last_sent_at
    if getattr(ver, "last_sent_at", None):
        delta = (now_datetime() - ver.last_sent_at).total_seconds()
        if delta < RESEND_COOLDOWN_SECONDS:
            wait = int(RESEND_COOLDOWN_SECONDS - delta)
            return {"ok": False, "message": f"Please wait {wait}s before requesting another OTP."}

    otp = _generate_otp()
    ver.otp_hash = sha256_hash(otp)
    ver.expires_at = add_to_date(now_datetime(), minutes=OTP_TTL_MINUTES)
    ver.is_used = 0
    ver.attempts = 0
    ver.last_sent_at = now_datetime()
    ver.save(ignore_permissions=True)

    _send_otp_email(email=email, otp=otp, full_name=user.first_name or "")

    return {"ok": True, "message": "New OTP sent to email."}
