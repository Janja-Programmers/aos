"""Framework authentication policies that must hold before AOS creates a session."""

from __future__ import annotations

from typing import Any

from aos.api.shared.responses import fail


def session_policy_error(user: str, *, login_manager: Any | None = None):
    """Enforce Frappe password-age policy before AOS creates a session."""
    force_reset = getattr(login_manager, "force_user_to_reset_password", None)
    if callable(force_reset) and force_reset():
        return fail("Password reset is required before login.", error="PASSWORD_RESET_REQUIRED")
    return None
