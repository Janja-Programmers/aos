import frappe
from frappe.exceptions import AuthenticationError

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail

from .constants import LOGIN_LIMIT_PER_HOUR_PER_EMAIL, LOGIN_LIMIT_PER_HOUR_PER_IP
from .users import get_user_payload
from .validators import normalize_email, validate_email


def login_impl(**kwargs):
    """Mobile-friendly login.

    Returns sid so Flutter can store it and send it as:
    Cookie: sid=<sid>
    """

    email = normalize_email(kwargs.get("email") or "")
    password = kwargs.get("password") or ""

    # rate limit by IP
    rl = rate_limit(
        key=f"aos:login:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=LOGIN_LIMIT_PER_HOUR_PER_IP,
        message="Too many login attempts. Please try again later.",
    )
    if rl:
        return rl

    # rate limit by email
    if email:
        rl2 = rate_limit(
            key=f"aos:login:email:{email}",
            ttl_seconds=60 * 60,
            limit=LOGIN_LIMIT_PER_HOUR_PER_EMAIL,
            message="Too many login attempts for this account. Please try again later.",
        )
        if rl2:
            return rl2

    if not email or not password:
        return fail("Email and password are required.", code="VALIDATION_ERROR")

    err = validate_email(email)
    if err:
        return err

    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if not user_name:
        # Do not leak account existence
        return fail("Invalid email or password.", code="INVALID_CREDENTIALS")

    enabled = frappe.db.get_value("User", user_name, "enabled")
    if int(enabled or 0) != 1:
        return fail("Please verify your email to continue.", code="NOT_VERIFIED")

    try:
        lm = frappe.local.login_manager
        lm.authenticate(user=user_name, pwd=password)
        lm.post_login()

        sid = getattr(frappe.session, "sid", None)
        if not sid:
            return fail("Login failed. Please try again.", code="LOGIN_FAILED")

        return ok(
            "Login successful.",
            data={
                "sid": sid,
                "user": get_user_payload(user_name),
            },
        )

    except AuthenticationError:
        return fail("Invalid email or password.", code="INVALID_CREDENTIALS")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Login Failed")
        return fail("Login failed. Please try again.", code="LOGIN_FAILED")


def me_impl(**_):
    """Session validation + bootstrap user payload.

    Requires Cookie: sid=<sid> header (or active session).
    """

    user_name = getattr(frappe.session, "user", None) or "Guest"

    if user_name == "Guest":
        return fail("Session invalid. Please login again.", code="SESSION_INVALID")

    try:
        enabled = frappe.db.get_value("User", user_name, "enabled")

        if int(enabled or 0) != 1:
            return fail("Account disabled.", code="ACCOUNT_DISABLED")

        return ok(
            "Session valid.",
            data={
                "sid": getattr(frappe.session, "sid", None),
                "user": get_user_payload(user_name),
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Me Failed")
        return fail("Session invalid. Please login again.", code="SESSION_INVALID")


def logout_impl(**_):
    """Logout current session.

    Flutter should also clear stored sid locally.
    """

    user_name = getattr(frappe.session, "user", None) or "Guest"

    if user_name == "Guest":
        return ok("Already logged out.")

    try:
        frappe.local.login_manager.logout()
        return ok("Logged out successfully.")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Logout Failed")
        return fail("Logout failed. Please try again.", code="LOGOUT_FAILED")
