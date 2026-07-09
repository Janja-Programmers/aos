"""Auth session endpoints: login, me, logout."""

from __future__ import annotations

import frappe
from frappe.exceptions import AuthenticationError
from frappe.utils.password import check_password

from aos.api.shared.account_status import deleted_account_response, ensure_account_active, get_account_state
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok, fail

from .account_helpers import ensure_auth_bootstrap, safe_log_auth_event, user_exists_by_identifier
from .constants import (
    LOGIN_LIMIT_PER_HOUR_PER_EMAIL,
    LOGIN_LIMIT_PER_HOUR_PER_IP,
)
from .serializers import serialize_auth_payload
from .validators import normalize_identifier, validate_client_type, validate_login_inputs


GENERIC_LOGIN_FAILURE = "Invalid credentials."


def _should_return_sid(client_type: str) -> bool:
    return client_type == "mobile"


def _rate_limit_login(identifier: str):
    rl = rate_limit(
        key=rate_limit_key("auth", "login", "ip", request_ip()),
        ttl_seconds=60 * 60,
        limit=LOGIN_LIMIT_PER_HOUR_PER_IP,
        message="Too many login attempts. Please try again later.",
    )
    if rl:
        return rl

    # Identifier is normalized, bounded, and hashed by rate_limit_key().
    return rate_limit(
        key=rate_limit_key("auth", "login", "identifier", identifier or "blank"),
        ttl_seconds=60 * 60,
        limit=LOGIN_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many login attempts. Please try again later.",
    )


def _check_password_without_login(user_name: str, password: str):
    try:
        check_password(user_name, password)
        return None
    except AuthenticationError:
        return fail(GENERIC_LOGIN_FAILURE, error="INVALID_CREDENTIALS")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Login Password Check Failed")
        return fail("Login failed. Please try again.", error="LOGIN_FAILED")


def login_impl(**kwargs):
    """Production login.

    Canonical request body:
        {"identifier": "email-or-username", "password": "...", "client_type": "mobile|web"}
    """

    identifier = normalize_identifier(kwargs.get("identifier") or "")
    password = kwargs.get("password") if isinstance(kwargs.get("password"), str) else ""

    rl = _rate_limit_login(identifier)
    if rl:
        safe_log_auth_event("AOS Login Rate Limited", identifier=identifier, reason="rate_limit")
        return rl

    validation_err = validate_login_inputs(identifier, password)
    if validation_err:
        safe_log_auth_event("AOS Login Malformed", identifier=identifier, reason="validation")
        return validation_err

    client_type, client_type_err = validate_client_type(kwargs.get("client_type"))
    if client_type_err:
        safe_log_auth_event("AOS Login Malformed", identifier=identifier, reason="client_type")
        return client_type_err

    user_name = user_exists_by_identifier(identifier)
    if not user_name:
        safe_log_auth_event("AOS Login Failed", identifier=identifier, reason="unknown_user")
        # Do not leak account existence.
        return fail(GENERIC_LOGIN_FAILURE, error="INVALID_CREDENTIALS")

    state = get_account_state(user_name)
    if state.get("is_deleted"):
        # Deleted accounts have a separate restore flow and intentionally return a
        # specific error so the app can route to restore UX.
        safe_log_auth_event("AOS Deleted Account Login", identifier=identifier, user=user_name, reason="deleted")
        return deleted_account_response(restorable=bool(state.get("can_restore")))

    active_err = ensure_account_active(user_name)
    if active_err:
        safe_log_auth_event("AOS Inactive Account Login", identifier=identifier, user=user_name, reason="inactive")
        return active_err

    password_err = _check_password_without_login(user_name, password)
    if password_err:
        safe_log_auth_event("AOS Login Failed", identifier=identifier, user=user_name, reason="bad_password")
        return password_err

    enabled = frappe.db.get_value("User", user_name, "enabled")
    if int(enabled or 0) != 1:
        # Only disclosed after the correct password is presented, avoiding
        # enumeration for random identifiers.
        safe_log_auth_event("AOS Disabled User Login", identifier=identifier, user=user_name, reason="disabled")
        return fail("Please verify your email to continue.", error="EMAIL_NOT_VERIFIED", http_status=403)

    pref, pref_err = ensure_auth_bootstrap(
        user_name,
        country=kwargs.get("country"),
        currency=kwargs.get("currency"),
        language=kwargs.get("language"),
    )
    if pref_err:
        return pref_err

    try:
        lm = frappe.local.login_manager
        lm.authenticate(user=user_name, pwd=password)
        lm.post_login()

        sid = getattr(frappe.session, "sid", None)
        if not sid:
            return fail("Login failed. Please try again.", error="LOGIN_FAILED")

        return ok(
            "Login successful.",
            data=serialize_auth_payload(
                user_name,
                sid=sid,
                include_sid=_should_return_sid(client_type),
            ),
        )

    except AuthenticationError:
        safe_log_auth_event("AOS Login Failed", identifier=identifier, user=user_name, reason="frappe_auth")
        return fail(GENERIC_LOGIN_FAILURE, error="INVALID_CREDENTIALS")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Login Failed")
        return fail("Login failed. Please try again.", error="LOGIN_FAILED")


def me_impl(**_):
    """Fetch the current authenticated session bootstrap payload.

    Requires an active Frappe session via Cookie: sid=<sid>. Guest receives a
    controlled SESSION_INVALID failure.
    """

    user_name = getattr(frappe.session, "user", None) or "Guest"

    if user_name == "Guest":
        return fail("Session invalid. Please login again.", error="SESSION_INVALID")

    try:
        state = get_account_state(user_name)
        if state.get("is_deleted"):
            return deleted_account_response(restorable=bool(state.get("can_restore")))

        active_err = ensure_account_active(user_name)
        if active_err:
            return active_err

        enabled = frappe.db.get_value("User", user_name, "enabled")
        if int(enabled or 0) != 1:
            return fail("Account disabled.", error="ACCOUNT_DISABLED", http_status=403)

        pref, pref_err = ensure_auth_bootstrap(user_name)
        if pref_err:
            return pref_err

        return ok(
            "Session fetched.",
            data=serialize_auth_payload(user_name, include_sid=False),
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Me Failed")
        return fail("Session invalid. Please login again.", error="SESSION_INVALID")


def logout_impl(**_):
    """Logout current session.

    Logout is intentionally idempotent: guests and already-expired sessions return
    success so clients can safely clear local state without branching.
    """

    user_name = getattr(frappe.session, "user", None) or "Guest"

    if user_name == "Guest":
        return ok("Already logged out.")

    try:
        frappe.local.login_manager.logout()
        return ok("Logged out successfully.")

    except Exception:
        frappe.log_error("Logout failed for current session user only; sid omitted.\n" + frappe.get_traceback(), "AOS Logout Failed")
        return fail("Logout failed. Please try again.", error="LOGOUT_FAILED")
