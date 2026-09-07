"""Canonical Authentication session endpoints: login, me, logout."""

from __future__ import annotations

import frappe
from frappe.exceptions import AuthenticationError
from frappe.utils.password import check_password, passlibctx

from aos.api.shared.account_status import deleted_account_response, ensure_account_active, get_account_state
from aos.api.shared.responses import fail, ok

from .account_helpers import assert_auth_bootstrap, safe_log_auth_event, user_for_email
from .constants import LOGIN_LIMIT_PER_HOUR_PER_EMAIL, LOGIN_LIMIT_PER_HOUR_PER_IP
from .contracts import reject_unknown_fields
from .locking import lock_user
from .rate_limits import auth_ip_limit, auth_rate_limit
from .serializers import serialize_auth_bootstrap, serialize_auth_payload, serialize_session
from .session_control import aos_session_creation_scope
from .session_policy import session_policy_error
from .two_factor import issue_two_factor_challenge, requires_two_factor
from .validators import require_identifier, require_password, validate_client_type
from .verification import has_pending_email_verification

GENERIC_LOGIN_FAILURE = "Invalid credentials."
_DUMMY_PASSWORD_HASH = passlibctx.hash("aos-auth-dummy-password")


def _rate_limit_login(identifier: str | None = None):
    limited = auth_ip_limit(
        operation="login",
        limit=LOGIN_LIMIT_PER_HOUR_PER_IP,
        message="Too many login attempts. Please try again later.",
    )
    if limited or not identifier:
        return limited
    return auth_rate_limit(
        operation="login",
        dimension="identifier",
        value=identifier,
        limit=LOGIN_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many login attempts. Please try again later.",
    )


def _dummy_password_work(password: str) -> None:
    """Reduce the obvious unknown-user vs wrong-password timing gap."""
    try:
        passlibctx.verify(password, _DUMMY_PASSWORD_HASH)
    except Exception:
        pass


def _inactive_error_after_password_proof(user_name: str):
    state = get_account_state(user_name)
    if state.get("is_deleted"):
        return deleted_account_response(restorable=bool(state.get("can_restore")))
    active_err = ensure_account_active(user_name, state=state)
    if active_err:
        return active_err
    enabled = frappe.db.get_value("User", user_name, "enabled")
    if int(enabled or 0) == 1:
        return None
    if has_pending_email_verification(user_name):
        return fail("Please verify your email to continue.", error="EMAIL_NOT_VERIFIED")
    return fail("Account disabled.", error="ACCOUNT_DISABLED")


def _prove_disabled_password(user_name: str, password: str):
    try:
        check_password(user_name, password)
        return None
    except AuthenticationError:
        return fail(GENERIC_LOGIN_FAILURE, error="INVALID_CREDENTIALS")
    except Exception as exc:
        from .observability import log_auth_exception

        log_auth_exception("AOS Login Password Check Failed", exc, operation="login_password_check")
        return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")


def login_impl(**kwargs):
    """Authenticate by normalized email and create a web-cookie or mobile session."""
    unknown = reject_unknown_fields(kwargs, {"identifier", "password", "client_type"})
    if unknown:
        return unknown

    identifier, identifier_err = require_identifier(kwargs.get("identifier"))
    limited = _rate_limit_login(identifier if identifier_err is None else None)
    if limited:
        safe_log_auth_event("AOS Login Rate Limited", identifier=identifier, reason="rate_limit")
        return limited
    if identifier_err:
        return identifier_err
    password, password_err = require_password(kwargs.get("password"))
    if password_err:
        return password_err
    client_type, client_err = validate_client_type(kwargs.get("client_type"))
    if client_err:
        return client_err

    user_name = user_for_email(identifier)
    if not user_name:
        _dummy_password_work(password)
        safe_log_auth_event("AOS Login Failed", identifier=identifier, reason="invalid_credentials")
        return fail(GENERIC_LOGIN_FAILURE, error="INVALID_CREDENTIALS")

    try:
        if not lock_user(user_name):
            return fail(GENERIC_LOGIN_FAILURE, error="INVALID_CREDENTIALS")
        enabled = int(frappe.db.get_value("User", user_name, "enabled") or 0)
        lm = None
        if enabled:
            lm = getattr(frappe.local, "login_manager", None)
            if lm is None:
                return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")
            try:
                # Exact email resolution happened above; no username/phone alias is public.
                lm.authenticate(user=user_name, pwd=password)
            except AuthenticationError:
                return fail(GENERIC_LOGIN_FAILURE, error="INVALID_CREDENTIALS")
        else:
            proof_err = _prove_disabled_password(user_name, password)
            if proof_err:
                return proof_err

        # Disabled/pending/deleted account classification does not require a
        # Frappe LoginManager because no session will be created for it. This
        # keeps those security-state responses deterministic even in isolated
        # service/test execution where request login machinery is absent.
        inactive = _inactive_error_after_password_proof(user_name)
        if inactive:
            return inactive
        if lm is None:
            lm = getattr(frappe.local, "login_manager", None)
            if lm is None:
                return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")
        policy_err = session_policy_error(user_name, login_manager=lm)
        if policy_err:
            return policy_err
        invariant = assert_auth_bootstrap(user_name, profile_exists=True)
        if invariant:
            return invariant
        if requires_two_factor(user_name):
            return issue_two_factor_challenge(user_name)

        # Finish all DB-backed response serialization before Frappe starts the
        # session; session creation may commit internally.
        bootstrap = serialize_auth_bootstrap(user_name)
        with aos_session_creation_scope():
            lm.post_login()

        sid = getattr(frappe.session, "sid", None)
        if not sid:
            return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")
        return ok(
            "Login successful.",
            data={
                "session": serialize_session(sid=sid, include_sid=client_type == "mobile"),
                **bootstrap,
            },
        )
    except AuthenticationError:
        return fail(GENERIC_LOGIN_FAILURE, error="INVALID_CREDENTIALS")
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Login Failed", exc, operation="login")
        return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")


def me_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, set())
    if unknown:
        return unknown
    user_name = getattr(frappe.session, "user", None) or "Guest"
    if user_name == "Guest":
        return fail("Session invalid. Please login again.", error="SESSION_INVALID")
    try:
        state = get_account_state(user_name)
        if state.get("is_deleted"):
            return deleted_account_response(restorable=bool(state.get("can_restore")))
        active_err = ensure_account_active(user_name, state=state)
        if active_err:
            return active_err
        if int(frappe.db.get_value("User", user_name, "enabled") or 0) != 1:
            return fail("Account disabled.", error="ACCOUNT_DISABLED")
        invariant = assert_auth_bootstrap(user_name, profile_exists=bool(state.get("exists")))
        if invariant:
            return invariant
        return ok("Session fetched.", data=serialize_auth_payload(user_name, include_sid=False))
    except Exception as exc:
        from .observability import log_auth_exception

        log_auth_exception("AOS Me Failed", exc, operation="me")
        return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")


def logout_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, set())
    if unknown:
        return unknown
    user_name = getattr(frappe.session, "user", None) or "Guest"
    if user_name == "Guest":
        return ok("Already logged out.")
    try:
        frappe.local.login_manager.logout()
        return ok("Logged out successfully.")
    except Exception as exc:
        from .observability import log_auth_exception

        log_auth_exception("AOS Logout Failed", exc, operation="logout")
        return fail("Logout failed. Please try again.", error="LOGOUT_FAILED")
