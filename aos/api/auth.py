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
LOGIN_LIMIT_PER_HOUR_PER_IP = 30
LOGIN_LIMIT_PER_HOUR_PER_EMAIL = 20

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _ok(message: str, data: dict | None = None):
    out = {"ok": True, "message": message}
    if data is not None:
        out["data"] = data
    return out


def _fail(message: str, code: str | None = None, data: dict | None = None):
    out = {"ok": False, "message": message}
    if code:
        out["code"] = code
    if data is not None:
        out["data"] = data
    return out


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
        return _fail("Full name is required.", code="VALIDATION_ERROR")
    if not email or not EMAIL_REGEX.match(email.strip().lower()):
        return _fail("A valid email is required.", code="VALIDATION_ERROR")
    if not password or len(password) < 8:
        return _fail("Password must be at least 8 characters long.", code="VALIDATION_ERROR")
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
        return _fail(message, code="RATE_LIMITED")
    return None


def _get_ver_doc(user_name: str):
    name = frappe.db.get_value("AOS Email Verification", {"user": user_name}, "name")
    if not name:
        return None
    return frappe.get_doc("AOS Email Verification", name)


def _get_user_payload(user_name: str) -> dict:
    # Keep it light and stable for mobile
    u = frappe.get_doc("User", user_name)
    return {
        "email": u.email,
        "full_name": (u.full_name or u.first_name or "").strip(),
        "enabled": int(u.enabled or 0),
    }


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
        return _fail("An account with this email already exists.", code="ALREADY_EXISTS")

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

        return _ok("OTP sent to email. Please verify to activate account.")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Register Failed")
        # If user got created, keep it disabled
        try:
            if frappe.db.exists("User", {"email": email}):
                frappe.db.set_value("User", email, "enabled", 0)
        except Exception:
            pass
        return _fail("Registration failed. Please try again.", code="REGISTER_FAILED")


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
        return _fail("Email and OTP are required.", code="VALIDATION_ERROR")

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        return _fail("Account not found.", code="NOT_FOUND")

    ver = _get_ver_doc(user_name)
    if not ver:
        return _fail("OTP not found. Please request a new OTP.", code="OTP_NOT_FOUND")

    if int(ver.is_used or 0) == 1:
        return _fail("OTP already used. Please request a new OTP.", code="OTP_USED")

    if now_datetime() > ver.expires_at:
        return _fail("OTP expired. Please request a new OTP.", code="OTP_EXPIRED")

    if int(ver.attempts or 0) >= MAX_ATTEMPTS:
        return _fail("Too many attempts. Please request a new OTP.", code="OTP_MAX_ATTEMPTS")

    if sha256_hash(otp) != ver.otp_hash:
        ver.attempts = int(ver.attempts or 0) + 1
        ver.save(ignore_permissions=True)
        return _fail("Invalid OTP.", code="OTP_INVALID")

    # Mark OTP used
    ver.is_used = 1
    ver.save(ignore_permissions=True)

    # Enable user
    user = frappe.get_doc("User", user_name)
    user.enabled = 1
    user.save(ignore_permissions=True)

    return _ok("Email verified. Account activated.")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def resend_email_otp(email: str):
    email = (email or "").strip().lower()
    if not email:
        return _fail("Email is required.", code="VALIDATION_ERROR")

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
        return _fail("Account not found.", code="NOT_FOUND")

    user = frappe.get_doc("User", user_name)
    if int(user.enabled or 0) == 1:
        return _ok("Account already active.")

    ver = _get_ver_doc(user_name)
    if not ver:
        return _fail("OTP record not found. Please register again.", code="OTP_RECORD_MISSING")

    # cooldown using last_sent_at
    if getattr(ver, "last_sent_at", None):
        delta = (now_datetime() - ver.last_sent_at).total_seconds()
        if delta < RESEND_COOLDOWN_SECONDS:
            wait = int(RESEND_COOLDOWN_SECONDS - delta)
            return _fail(f"Please wait {wait}s before requesting another OTP.", code="COOLDOWN", data={"wait_seconds": wait})

    otp = _generate_otp()
    ver.otp_hash = sha256_hash(otp)
    ver.expires_at = add_to_date(now_datetime(), minutes=OTP_TTL_MINUTES)
    ver.is_used = 0
    ver.attempts = 0
    ver.last_sent_at = now_datetime()
    ver.save(ignore_permissions=True)

    _send_otp_email(email=email, otp=otp, full_name=user.first_name or "")

    return _ok("New OTP sent to email.")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def login(email: str, password: str):
    """
    Mobile-friendly login wrapper.
    Returns sid so Flutter can store it and send it as: Cookie: sid=<sid>
    """
    email = (email or "").strip().lower()
    password = password or ""

    # rate limit by IP
    rl = _rate_limit(
        key=f"aos:login:ip:{_request_ip()}",
        ttl_seconds=60 * 60,
        limit=LOGIN_LIMIT_PER_HOUR_PER_IP,
        message="Too many login attempts. Please try again later."
    )
    if rl:
        return rl

    # rate limit by email (only if email present)
    if email:
        rl2 = _rate_limit(
            key=f"aos:login:email:{email}",
            ttl_seconds=60 * 60,
            limit=LOGIN_LIMIT_PER_HOUR_PER_EMAIL,
            message="Too many login attempts for this account. Please try again later."
        )
        if rl2:
            return rl2

    if not email or not password:
        return _fail("Email and password are required.", code="VALIDATION_ERROR")

    if not EMAIL_REGEX.match(email):
        return _fail("A valid email is required.", code="VALIDATION_ERROR")

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        # don't leak account existence
        return _fail("Invalid email or password.", code="INVALID_CREDENTIALS")

    enabled = frappe.db.get_value("User", user_name, "enabled")
    if int(enabled or 0) != 1:
        return _fail("Please verify your email to continue.", code="NOT_VERIFIED")

    try:
        lm = frappe.local.login_manager
        lm.authenticate(user=user_name, pwd=password)
        lm.post_login()

        sid = getattr(frappe.session, "sid", None)
        if not sid:
            # very rare, but safe-guard
            return _fail("Login failed. Please try again.", code="LOGIN_FAILED")

        return _ok(
            "Login successful.",
            data={
                "sid": sid,
                "user": _get_user_payload(user_name),
            }
        )

    except Exception as e:
        # Frappe throws different exceptions depending on version/config
        msg = (str(e) or "").lower()
        if "password" in msg or "invalid" in msg or "authentication" in msg:
            return _fail("Invalid email or password.", code="INVALID_CREDENTIALS")

        frappe.log_error(frappe.get_traceback(), "AOS Login Failed")
        return _fail("Login failed. Please try again.", code="LOGIN_FAILED")


@frappe.whitelist(methods=["GET"])
def me():
    """
    Session validation + bootstrap user payload.
    Requires Cookie: sid=<sid> header (or an active session).
    """
    user_name = getattr(frappe.session, "user", None) or "Guest"
    if user_name == "Guest":
        return _fail("Session invalid. Please login again.", code="SESSION_INVALID")

    try:
        enabled = frappe.db.get_value("User", user_name, "enabled")
        if int(enabled or 0) != 1:
            return _fail("Account disabled.", code="ACCOUNT_DISABLED")

        return _ok(
            "Session valid.",
            data={
                "sid": getattr(frappe.session, "sid", None),
                "user": _get_user_payload(user_name),
            }
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Me Failed")
        return _fail("Session invalid. Please login again.", code="SESSION_INVALID")


@frappe.whitelist(methods=["POST"])
def logout():
    """
    Logout current session.
    Flutter should also clear stored sid locally.
    """
    user_name = getattr(frappe.session, "user", None) or "Guest"
    if user_name == "Guest":
        return _ok("Already logged out.")

    try:
        # Frappe login_manager logout handles session cleanup
        frappe.local.login_manager.logout()
        return _ok("Logged out successfully.")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Logout Failed")
        return _fail("Logout failed. Please try again.", code="LOGOUT_FAILED")
