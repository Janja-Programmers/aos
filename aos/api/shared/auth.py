"""Shared auth/session helpers for whitelisted APIs."""

from __future__ import annotations

import frappe

from .responses import fail


def current_user() -> str:
    """Return current session user or 'Guest'."""
    return getattr(frappe.session, "user", None) or "Guest"


def require_login():
    """
    Return (user, None) when logged in and preference exists,
    else (None, fail...).

    Enforces system invariant:
    Every authenticated user must have an AOS User Preference.
    """
    user = current_user()

    if user == "Guest":
        return None, fail(
            "Please login to continue.",
            code="UNAUTHORIZED"
        )

    # Enforce preference existence
    pref_exists = frappe.db.exists(
        "AOS User Preference",
        {"user": user}
    )

    if not pref_exists:
        return None, fail(
            "User preference not configured.",
            code="PREFERENCE_MISSING"
        )

    return user, None
