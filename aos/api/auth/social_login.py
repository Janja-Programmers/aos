"""Shared social-login orchestration for Google and Apple OIDC identities."""

from __future__ import annotations

import frappe

from aos.api.shared.account_status import deleted_account_response, ensure_account_active, get_account_state
from aos.api.shared.responses import fail, ok

from .account_helpers import account_enabled, create_auth_bootstrap, load_auth_bootstrap_preference, user_for_email
from .constants import SOCIAL_LOGIN_LIMIT_PER_HOUR_PER_SUBJECT
from .locking import lock_user
from .oidc import OIDCDependencyError, OIDCTokenError
from .rate_limits import auth_ip_limit, auth_rate_limit
from .serializers import serialize_auth_bootstrap, serialize_session
from .session_control import aos_session_creation_scope
from .session_policy import session_policy_error
from .social_identity import bind_identity, get_bound_user
from .two_factor import issue_two_factor_challenge, requires_two_factor
from .user_controller import mark_aos_managed_website_user_creation
from .validators import optional_bootstrap_inputs, require_email, require_token, validate_client_type


def _account_ready(user: str):
    state = get_account_state(user)
    if state.get("is_deleted"):
        return None, deleted_account_response(restorable=bool(state.get("can_restore")))
    active = ensure_account_active(user, state=state)
    if active:
        return None, active
    if not account_enabled(user, state=state):
        return None, fail("Account disabled.", error="ACCOUNT_DISABLED")
    return load_auth_bootstrap_preference(user, profile_exists=bool(state.get("exists")))


def _create_social_user(*, email: str, full_name: str, bootstrap: dict):
    user = frappe.new_doc("User")
    user.email = email
    user.first_name = full_name or email.split("@")[0]
    user.enabled = 1
    user.user_type = "Website User"
    user.send_welcome_email = 0
    user.flags.no_welcome_mail = True
    mark_aos_managed_website_user_creation(user)
    user.insert(ignore_permissions=True)
    pref, err = create_auth_bootstrap(user.name, **bootstrap)
    if err:
        return None, None, err
    return user.name, pref, None


def social_login_impl(
    *,
    provider: str,
    verify_token,
    audiences: list[str],
    request_kwargs: dict,
    ip_limit: int,
):
    token, err = require_token(request_kwargs.get("id_token"), "id_token")
    if err:
        return err
    client_type, err = validate_client_type(request_kwargs.get("client_type"))
    if err:
        return err
    bootstrap, err = optional_bootstrap_inputs(request_kwargs)
    if err:
        return err
    limited = auth_ip_limit(
        operation=f"{provider}_login",
        limit=ip_limit,
        message="Too many login attempts. Please try again later.",
    )
    if limited:
        return limited
    if not audiences:
        return fail(f"{provider.title()} authentication is not configured.", error="CONFIG_ERROR")
    try:
        claims = verify_token(token, audiences)
    except OIDCDependencyError:
        return fail("Authentication provider temporarily unavailable.", error="SERVICE_UNAVAILABLE")
    except OIDCTokenError as exc:
        if exc.code == "TOKEN_EXPIRED":
            return fail("Identity token expired.", error="TOKEN_EXPIRED")
        if exc.code == "EMAIL_NOT_VERIFIED":
            return fail("Provider email is not verified.", error="EMAIL_NOT_VERIFIED")
        return fail("Invalid identity token.", error="TOKEN_INVALID")
    except Exception as exc:
        from .observability import log_auth_exception

        log_auth_exception("AOS Social Token Verification Failed", exc, operation=f"{provider}_token_verify")
        return fail("Authentication provider temporarily unavailable.", error="SERVICE_UNAVAILABLE")

    subject = str(claims.get("sub") or "").strip()
    if not subject:
        return fail("Invalid identity token.", error="TOKEN_INVALID")
    limited = auth_rate_limit(
        operation=f"{provider}_login",
        dimension="subject",
        value=subject,
        limit=SOCIAL_LOGIN_LIMIT_PER_HOUR_PER_SUBJECT,
        message="Too many login attempts. Please try again later.",
    )
    if limited:
        return limited

    bound_user = get_bound_user(provider, subject)
    email = str(claims.get("email") or "").strip().lower()
    full_name = str(claims.get("name") or claims.get("given_name") or "").strip()[:140]
    try:
        if bound_user:
            user_name = bound_user
            lock_user(user_name)
            preference, ready_error = _account_ready(user_name)
            if ready_error:
                return ready_error
        else:
            if provider == "apple" and not email:
                # Apple may omit email after first authorization; without an
                # existing subject binding there is no safe account to infer.
                return fail("Apple account email is required for first sign-in.", error="TOKEN_INVALID")
            email, email_err = require_email(email, field="email")
            if email_err:
                return fail("Provider account email is invalid.", error="TOKEN_INVALID")
            user_name = user_for_email(email)
            if user_name:
                lock_user(user_name)
                preference, ready_error = _account_ready(user_name)
                if ready_error:
                    return ready_error
            else:
                try:
                    user_name, preference, bootstrap_err = _create_social_user(
                        email=email,
                        full_name=full_name,
                        bootstrap=bootstrap,
                    )
                except frappe.DuplicateEntryError:
                    user_name = user_for_email(email)
                    if not user_name:
                        raise
                    lock_user(user_name)
                    bootstrap_err = None
                    preference, ready_error = _account_ready(user_name)
                    if ready_error:
                        return ready_error
                if bootstrap_err:
                    frappe.db.rollback()
                    return bootstrap_err
            bind_err = bind_identity(provider=provider, subject=subject, user=user_name)
            if bind_err:
                frappe.db.rollback()
                return bind_err

        policy_err = session_policy_error(user_name)
        if policy_err:
            return policy_err
        if requires_two_factor(user_name):
            return issue_two_factor_challenge(user_name)

        # Resolve the entire DB-backed response before session creation. Frappe
        # may commit when starting a session, so no fallible bootstrap query
        # should remain after this point.
        bootstrap_payload = serialize_auth_bootstrap(user_name, preference=preference)
        lm = frappe.local.login_manager
        with aos_session_creation_scope():
            lm.login_as(user_name)
        sid = getattr(frappe.session, "sid", None)
        if not sid:
            return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")
        return ok(
            "Login successful.",
            data={
                "session": serialize_session(sid=sid, include_sid=client_type == "mobile"),
                **bootstrap_payload,
            },
        )
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception(f"AOS {provider.title()} Login Failed", exc, operation=f"{provider}_login")
        return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")
