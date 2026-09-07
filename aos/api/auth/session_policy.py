"""Framework authentication policies that must hold before AOS creates a session."""

from __future__ import annotations

from typing import Any

from aos.api.shared.responses import fail


def session_policy_error(user: str, *, login_manager: Any | None = None):
    """Fail closed when Frappe requires an additional authentication policy.

    AOS does not silently bypass Frappe role-based 2FA. The current public AOS
    contract has no second-factor continuation endpoint, so accounts covered by
    that framework policy are explicitly denied until the client/backend adds a
    canonical continuation flow. Password-age enforcement is relevant only to
    password login, where ``login_manager`` has authenticated the credential.
    """
    from frappe.twofactor import should_run_2fa

    if should_run_2fa(user):
        return fail(
            "Two-factor authentication is required for this account.",
            error="TWO_FACTOR_REQUIRED",
        )

    force_reset = getattr(login_manager, "force_user_to_reset_password", None)
    if callable(force_reset) and force_reset():
        return fail(
            "Password reset is required before login.",
            error="PASSWORD_RESET_REQUIRED",
        )
    return None
