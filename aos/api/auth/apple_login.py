"""Apple social login implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.account_status import ensure_account_active, get_account_state
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok, fail
from aos.utils.aos_settings import get_apple_bundle_id

from .constants import APPLE_LOGIN_LIMIT_PER_HOUR_PER_IP
from .account_helpers import ensure_aos_profile, ensure_user_preference, safe_log_auth_event
from .serializers import serialize_auth_payload
from .validators import optional_bootstrap_inputs, require_email, require_string, require_token, validate_client_type
from .apple_jwt import verify_apple_id_token


def _include_sid(client_type: str) -> bool:
    return client_type == "mobile"


def _get_apple_bundle_id():
    """Read Apple Bundle ID from the AOS Settings snapshot."""
    return get_apple_bundle_id()


def _bootstrap_new_apple_user(email: str, bootstrap_inputs: dict):
    """Create all AOS identity rows for a new Apple user atomically."""

    try:
        user = frappe.new_doc("User")
        user.email = email
        user.first_name = email.split("@")[0]
        user.enabled = 1
        user.user_type = "Website User"
        user.send_welcome_email = 0
        user.flags.no_welcome_mail = True
        user.insert(ignore_permissions=True)

        user_name = user.name
        ensure_aos_profile(user_name)

        pref, pref_err = ensure_user_preference(user_name, **bootstrap_inputs)
        if pref_err:
            frappe.db.rollback()
            return None, pref_err

        # Account bootstrap is durable before any session is created.
        frappe.db.commit()
        return user_name, None

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Apple User Bootstrap Failed")
        frappe.db.rollback()
        return None, fail("Could not create account.", error="USER_CREATE_FAILED")


def _ensure_existing_apple_user_ready(user_name: str, email: str, bootstrap_inputs: dict):
    """Validate account state and repair preference for an existing user."""

    state = get_account_state(user_name)
    if state.get("is_deleted"):
        return fail(
            "This account was previously deleted. Please restore it instead.",
            error="ACCOUNT_DELETED_RESTORABLE" if state.get("can_restore") else "ACCOUNT_DELETED",
            data={"can_restore": bool(state.get("can_restore"))},
        )

    active_err = ensure_account_active(user_name)
    if active_err:
        return active_err

    enabled = frappe.db.get_value("User", user_name, "enabled")
    if int(enabled or 0) != 1:
        safe_log_auth_event("AOS Social Login Disabled User", identifier=email, user=user_name, reason="disabled")
        return fail("Account disabled.", error="ACCOUNT_DISABLED", http_status=403)

    pref, pref_err = ensure_user_preference(user_name, **bootstrap_inputs)
    if pref_err:
        return pref_err

    frappe.db.commit()
    return None


def apple_login_impl(**kwargs):
    """
    Login/Register using Apple Sign-In.

    - Verifies Apple identity token
    - Atomically creates User + AOS Profile + AOS User Preference for new users
    - Repairs missing preference for existing users without deleting existing state
    - Creates session only after required AOS identity rows are durable
    """

    id_token, token_err = require_token(kwargs.get("id_token"), "id_token")
    if token_err:
        return token_err

    client_type, client_type_err = validate_client_type(kwargs.get("client_type"))
    if client_type_err:
        return client_type_err

    bootstrap_inputs, bootstrap_err = optional_bootstrap_inputs(kwargs)
    if bootstrap_err:
        return bootstrap_err

    rl = rate_limit(
        key=rate_limit_key("auth", "apple", "ip", request_ip()),
        ttl_seconds=60 * 60,
        limit=APPLE_LOGIN_LIMIT_PER_HOUR_PER_IP,
        message="Too many attempts. Please try again later.",
    )
    if rl:
        return rl

    bundle_id = _get_apple_bundle_id()
    if not bundle_id:
        return fail("Apple Bundle ID not configured.", error="CONFIG_ERROR")

    try:
        claims = verify_apple_id_token(
            id_token=id_token,
            audience=bundle_id,
        )

    except ValueError as e:
        code = str(e) or "TOKEN_INVALID"

        if code == "TOKEN_EXPIRED":
            return fail("Apple token expired.", error="TOKEN_EXPIRED", http_status=401)

        if code in {"AUD_INVALID", "ISS_INVALID"}:
            return fail("Apple token not allowed.", error="TOKEN_INVALID", http_status=401)

        return fail("Invalid Apple token.", error="TOKEN_INVALID", http_status=401)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Apple Token Verify Failed")
        return fail("Could not verify Apple token.", error="TOKEN_VERIFY_FAILED")

    email, email_err = require_email(claims.get("email"), field="email")
    if email_err:
        return fail("Email not provided by Apple.", error="EMAIL_MISSING")

    apple_sub, apple_sub_err = require_string(claims.get("sub"), "sub", max_length=255)
    if apple_sub_err:
        return fail("Apple subject missing.", error="TOKEN_INVALID")

    user_name = frappe.db.get_value("User", {"email": email}, "name")

    if user_name:
        ready_err = _ensure_existing_apple_user_ready(user_name, email, bootstrap_inputs)
        if ready_err:
            return ready_err
    else:
        user_name, bootstrap_err = _bootstrap_new_apple_user(email, bootstrap_inputs)
        if bootstrap_err:
            return bootstrap_err

    try:
        lm = frappe.local.login_manager
        lm.login_as(user_name)

        sid = getattr(frappe.session, "sid", None)
        if not sid:
            return fail("Login failed.", error="LOGIN_FAILED", http_status=401)

        return ok(
            "Login successful.",
            data=serialize_auth_payload(user_name, sid=sid, include_sid=_include_sid(client_type)),
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Apple Login Failed")
        return fail("Login failed.", error="LOGIN_FAILED", http_status=401)
