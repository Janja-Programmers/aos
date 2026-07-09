from __future__ import annotations

import frappe
from frappe.utils.password import check_password, update_password

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok, fail

from .constants import CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_USER, CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_IP
from .validators import require_password, validate_password_strength


def change_password_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    current_password, current_err = require_password(kwargs.get("current_password"), "current_password")
    if current_err:
        return current_err

    new_password, new_err = require_password(kwargs.get("new_password"), "new_password")
    if new_err:
        return new_err

    confirm_password, confirm_err = require_password(kwargs.get("confirm_password"), "confirm_password")
    if confirm_err:
        return confirm_err

    if new_password != confirm_password:
        return fail("Passwords do not match.", error="PASSWORD_MISMATCH")

    pw_err = validate_password_strength(new_password)
    if pw_err:
        return pw_err

    rl = rate_limit(
        key=rate_limit_key("auth", "change_password", "user", current_user),
        ttl_seconds=60 * 60,
        limit=CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_USER,
        message="Too many attempts. Please try again later.",
    )
    if rl:
        return rl

    rl2 = rate_limit(
        key=rate_limit_key("auth", "change_password", "ip", request_ip()),
        ttl_seconds=60 * 60,
        limit=CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_IP,
        message="Too many attempts. Please try again later.",
    )
    if rl2:
        return rl2

    try:
        check_password(current_user, current_password)
    except frappe.AuthenticationError:
        return fail("Current password is incorrect.", error="FORBIDDEN")
    except Exception:
        frappe.log_error(title="AOS Change Password Error", message=frappe.get_traceback())
        return fail("Could not change password. Please try again.", error="INTERNAL_ERROR")

    try:
        update_password(current_user, new_password)
    except Exception:
        frappe.log_error(title="AOS Set Password Error", message=frappe.get_traceback())
        return fail("Could not change password. Please try again.", error="INTERNAL_ERROR")

    return ok("Password changed successfully.")
