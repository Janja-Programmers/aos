"""Shared password-policy and credential-write primitives for AOS Authentication."""

from __future__ import annotations

import frappe
from frappe.utils import today
from frappe.utils.password import is_password_reused, update_password

from aos.api.shared.responses import fail

from .validators import validate_password_strength


def validate_new_password(user: str, password: str, *, field: str = "new_password"):
    """Validate configured strength and reject reuse of the current credential."""
    policy = validate_password_strength(password, user_data=(user,))
    if policy:
        data = policy.get("data") if isinstance(policy, dict) else None
        if isinstance(data, dict) and data.get("field") == "password":
            data["field"] = field
        return policy
    try:
        if is_password_reused(user, password):
            return fail(
                "New password must be different from the current password.",
                error="PASSWORD_REUSED",
                data={"field": field},
            )
    except Exception as exc:
        # Do not log the credential or a traceback carrying local password data.
        frappe.log_error(
            message=f"Password reuse check failed: {type(exc).__name__}",
            title="AOS Auth Password Reuse Check Failed",
        )
        return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")
    return None


def set_user_password(user: str, password: str) -> None:
    """Write a Frappe password inside the caller's existing transaction.

    Session semantics remain owned by AOS so revocation can be coordinated with
    verification-token consumption atomically instead of allowing framework
    convenience helpers to commit/clear sessions independently.
    """
    update_password(user, password, logout_all_sessions=False)
    frappe.db.set_value(
        "User",
        user,
        {"last_password_reset_date": today(), "reset_password_key": ""},
        update_modified=False,
    )
