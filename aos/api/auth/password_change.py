import frappe
from frappe.utils.password import check_password, update_password
from frappe.utils import now_datetime

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from .constants import CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_USER, CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_IP
from .validators import validate_password_strength


def change_password_impl(current_password: str, new_password: str, confirm_password: str):
    # Auth required
    user = frappe.session.user
    if not user or user == "Guest":
        return fail("Unauthorized.", code="UNAUTHORIZED")

    current_password = (current_password or "")
    new_password = (new_password or "")
    confirm_password = (confirm_password or "")

    if not current_password:
        return fail("Current password is required.", code="VALIDATION_ERROR")
    if not new_password or not confirm_password:
        return fail("New password and confirm password are required.", code="VALIDATION_ERROR")
    if new_password != confirm_password:
        return fail("Passwords do not match.", code="PASSWORD_MISMATCH")

    pw_err = validate_password_strength(new_password)
    if pw_err:
        return pw_err

    # Rate limit by user + IP
    rl = rate_limit(
        key=f"aos:change_pw:user:{user}",
        ttl_seconds=60 * 60,
        limit=CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_USER,
        message="Too many attempts. Please try again later.",
    )
    if rl:
        return rl

    rl2 = rate_limit(
        key=f"aos:change_pw:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_IP,
        message="Too many attempts. Please try again later.",
    )
    if rl2:
        return rl2

    # Verify current password
    try:
        check_password(user, current_password)
    except frappe.AuthenticationError:
        return fail("Current password is incorrect.", code="FORBIDDEN")
    except Exception:
        # Avoid leaking internal errors
        frappe.log_error(title="AOS Change Password Error", message=frappe.get_traceback())
        return fail("Could not change password. Please try again.", code="INTERNAL_ERROR", http_status=500)

    # Update password
    try:
        update_password(user, new_password)
    except Exception:
        frappe.log_error(title="AOS Set Password Error", message=frappe.get_traceback())
        return fail("Could not change password. Please try again.", code="INTERNAL_ERROR", http_status=500)

    return ok("Password changed successfully.")
