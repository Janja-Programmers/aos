"""Authenticated password change."""

from __future__ import annotations

import frappe
from frappe.utils.password import check_password

from aos.api.shared.auth import require_login
from aos.api.shared.responses import fail, ok

from .constants import CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_IP, CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_USER
from .contracts import reject_unknown_fields
from .locking import lock_user
from .rate_limits import auth_ip_limit, auth_rate_limit
from .passwords import set_user_password, validate_new_password
from .session_control import revoke_all_sessions
from .validators import require_password


def change_password_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"current_password", "new_password", "confirm_password"})
    if unknown:
        return unknown
    current_user, err = require_login()
    if err:
        return err
    current_password, err = require_password(kwargs.get("current_password"), "current_password")
    if err:
        return err
    new_password, err = require_password(kwargs.get("new_password"), "new_password")
    if err:
        return err
    confirm_password, err = require_password(kwargs.get("confirm_password"), "confirm_password")
    if err:
        return err
    if new_password != confirm_password:
        return fail("Passwords do not match.", error="PASSWORD_MISMATCH")
    limited = auth_rate_limit(
        operation="change_password", dimension="user", value=current_user,
        limit=CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_USER, message="Too many attempts. Please try again later.",
    ) or auth_ip_limit(
        operation="change_password", limit=CHANGE_PASSWORD_LIMIT_PER_HOUR_PER_IP,
        message="Too many attempts. Please try again later.",
    )
    if limited:
        return limited
    try:
        lock_user(current_user)
        try:
            check_password(current_user, current_password)
        except frappe.AuthenticationError:
            return fail("Current password is incorrect.", error="FORBIDDEN")
        policy = validate_new_password(current_user, new_password)
        if policy:
            return policy
        set_user_password(current_user, new_password)
        current_sid = str(getattr(frappe.session, "sid", "") or "")
        revoke_all_sessions(current_user, keep_sid=current_sid or None)
        return ok("Password changed successfully.")
    except frappe.ValidationError:
        frappe.db.rollback()
        return fail("Password does not meet the required policy.", error="VALIDATION_ERROR", data={"field": "new_password"})
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Change Password Failed", exc, operation="change_password")
        return fail("Could not change password. Please try again.", error="INTERNAL_ERROR")
