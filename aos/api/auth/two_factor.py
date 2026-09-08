"""AOS second-factor continuation for Website User logins.

Frappe decides whether the user is covered by a 2FA policy. AOS owns the public
continuation contract so clients do not depend on Frappe's internal form_dict,
tmp_id, or Desk login response shape.
"""

from __future__ import annotations

import frappe
from frappe.exceptions import AuthenticationError
from frappe.utils import now_datetime

from aos.api.shared.account_status import ensure_account_active, get_account_state
from aos.api.shared.responses import fail, ok

from .account_helpers import load_auth_bootstrap_preference
from .constants import TWO_FACTOR_VERIFY_LIMIT_PER_HOUR_PER_CHALLENGE, TWO_FACTOR_VERIFY_LIMIT_PER_HOUR_PER_IP
from .contracts import reject_unknown_fields
from .locking import lock_user
from .otp_service import issue_otp, otp_is_active, resend_allowed, verify_public_otp
from .rate_limits import auth_ip_limit, auth_rate_limit
from .serializers import serialize_auth_bootstrap, serialize_session
from .session_control import aos_session_creation_scope
from .session_policy import session_policy_error
from .validators import require_otp, require_token, validate_client_type
from .verification import (
    TWO_FACTOR_PURPOSE,
    compute_continuation_expiry,
    ensure_ver_doc,
    generate_continuation_token,
    token_digest,
)


def requires_two_factor(user: str) -> bool:
    from frappe.twofactor import should_run_2fa

    return bool(should_run_2fa(user))


def issue_two_factor_challenge(user: str):
    """Queue an email OTP and return only a high-entropy continuation token."""
    ver = ensure_ver_doc(user, purpose=TWO_FACTOR_PURPOSE, for_update=True)
    if not ver:
        return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")
    profile_name = frappe.db.get_value("AOS Profile", {"user": user}, "display_name") or ""
    # Repeated successful first-factor requests within the resend cooldown do
    # not create an email flood. A fresh continuation token may reuse the still
    # valid OTP; used/expired/missing OTP state always receives a new code.
    if not otp_is_active(ver) or resend_allowed(ver):
        issue_otp(ver, email=user, full_name=profile_name, purpose=TWO_FACTOR_PURPOSE)
    token = generate_continuation_token()
    ver.continuation_token_hash = token_digest(token)
    ver.continuation_expires_at = compute_continuation_expiry()
    ver.save(ignore_permissions=True)
    return fail(
        "Two-factor authentication is required.",
        error="TWO_FACTOR_REQUIRED",
        data={"challenge_token": token, "method": "email", "expires_in_seconds": 900},
        http_status=403,
    )


def _challenge_candidate(challenge_token: str):
    """Resolve the challenge without taking a row lock so User can be locked first."""
    digest = token_digest(challenge_token)
    return frappe.db.get_value(
        "AOS Auth Challenge",
        {"purpose": TWO_FACTOR_PURPOSE, "continuation_token_hash": digest},
        ["name", "user"],
        as_dict=True,
    )


def _lock_challenge(name: str, challenge_token: str):
    """Lock the challenge after User, preserving Authentication lock order."""
    digest = token_digest(challenge_token)
    rows = frappe.db.sql(
        """
        SELECT name FROM `tabAOS Auth Challenge`
        WHERE name = %s AND purpose = %s AND continuation_token_hash = %s
        LIMIT 1 FOR UPDATE
        """,
        (name, TWO_FACTOR_PURPOSE, digest),
        as_dict=True,
    )
    return frappe.get_doc("AOS Auth Challenge", rows[0].name) if rows else None


def verify_two_factor_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"challenge_token", "otp", "client_type"})
    if unknown:
        return unknown
    challenge_token, err = require_token(kwargs.get("challenge_token"), "challenge_token")
    if err:
        return err
    otp, err = require_otp(kwargs.get("otp"))
    if err:
        return err
    client_type, err = validate_client_type(kwargs.get("client_type"))
    if err:
        return err

    limited = auth_ip_limit(
        operation="two_factor_verify",
        limit=TWO_FACTOR_VERIFY_LIMIT_PER_HOUR_PER_IP,
        message="Too many verification attempts. Please try again later.",
    ) or auth_rate_limit(
        operation="two_factor_verify",
        dimension="challenge",
        value=token_digest(challenge_token),
        limit=TWO_FACTOR_VERIFY_LIMIT_PER_HOUR_PER_CHALLENGE,
        message="Too many verification attempts. Please try again later.",
    )
    if limited:
        return limited

    try:
        candidate = _challenge_candidate(challenge_token)
        if not candidate:
            return fail("Invalid or expired two-factor challenge.", error="TOKEN_INVALID")
        user = str(candidate.user or "").strip()
        if not user or not lock_user(user):
            return fail("Invalid or expired two-factor challenge.", error="TOKEN_INVALID")
        ver = _lock_challenge(str(candidate.name), challenge_token)
        if not ver or not ver.continuation_expires_at or now_datetime() > ver.continuation_expires_at:
            return fail("Invalid or expired two-factor challenge.", error="TOKEN_INVALID")
        state = get_account_state(user)
        active_err = ensure_account_active(user, state=state)
        if active_err:
            return active_err
        if not state.get("enabled"):
            return fail("Account disabled.", error="ACCOUNT_DISABLED")
        otp_err = verify_public_otp(ver, otp, consume=False)
        if otp_err:
            return otp_err

        lm = getattr(frappe.local, "login_manager", None)
        if lm is None:
            return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")
        lm.user = user
        policy_err = session_policy_error(user, login_manager=lm)
        if policy_err:
            return policy_err
        preference, invariant = load_auth_bootstrap_preference(
            user,
            profile_exists=bool(state.get("exists")),
        )
        if invariant:
            return invariant
        bootstrap = serialize_auth_bootstrap(user, preference=preference)

        # Consume both factors only after all pre-session checks pass. Frappe may
        # commit while making the session, so successful challenge state must be
        # irreversibly consumed before post_login can create that session.
        ver.is_used = 1
        ver.otp_password_hash = ""
        ver.continuation_token_hash = ""
        ver.continuation_expires_at = None
        ver.save(ignore_permissions=True)

        with aos_session_creation_scope():
            lm.post_login()
        sid = getattr(frappe.session, "sid", None)
        if not sid:
            return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")
        return ok(
            "Login successful.",
            data={"session": serialize_session(sid=sid, include_sid=client_type == "mobile"), **bootstrap},
        )
    except AuthenticationError:
        return fail("Invalid or expired two-factor challenge.", error="TOKEN_INVALID")
    except Exception as exc:
        frappe.db.rollback()
        from .observability import log_auth_exception

        log_auth_exception("AOS Two Factor Verify Failed", exc, operation="two_factor_verify")
        return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")
