"""Shared auth/session helpers for whitelisted APIs."""

from __future__ import annotations

import frappe

from .account_status import ensure_account_active
from .responses import fail


GUEST_USER = "Guest"


def session_user() -> str:
    """Return the raw current Frappe session user or 'Guest'.

    This intentionally does not check AOS account status. Use this when you need
    to know who owns the current session, especially inside require_login().
    """
    return getattr(frappe.session, "user", None) or GUEST_USER


def is_enabled_user(user: str) -> bool:
    if not user or user == GUEST_USER:
        return False
    enabled = frappe.db.get_value("User", user, "enabled")
    return int(enabled or 0) == 1


def optional_active_user() -> str | None:
    """Return the current user only if the session belongs to an active account.

    Public/guest endpoints should use this helper so stale sessions from
    deleted/disabled/suspended accounts are treated like guests instead of
    receiving personalized state such as wishlist, following, owner flags, market
    prefs, or private audience access.
    """
    user = session_user()

    if not user or user == GUEST_USER:
        return None

    if ensure_account_active(user):
        return None

    if not is_enabled_user(user):
        return None

    return user


def current_user() -> str:
    """Return the current active user or 'Guest'.

    Historically public endpoints called current_user() directly. Keeping this
    function as the safe optional-auth resolver prevents stale deleted/disabled
    sessions from being treated as logged-in viewers.
    """
    return optional_active_user() or GUEST_USER


def require_authenticated_user():
    """
    Return (user, None) when logged in and active, else (None, fail...).

    Unlike require_login(), this intentionally does not require an AOS User
    Preference. Use it for infrastructure-style authenticated APIs and account
    setup endpoints where ownership is enough and missing preferences may need to
    be repaired.
    """
    user = session_user()

    if user == GUEST_USER:
        return None, fail("Please login to continue.", error="UNAUTHORIZED")

    active_err = ensure_account_active(user)
    if active_err:
        return None, active_err

    if not is_enabled_user(user):
        return None, fail("Account disabled.", error="ACCOUNT_DISABLED", http_status=403)

    return user, None


def require_login():
    """
    Return (user, None) when logged in, active, and preference exists.

    Enforces system invariants:
    - Every authenticated user must be a non-Guest session user.
    - Deleted/suspended accounts cannot use authenticated APIs.
    - Disabled accounts cannot use authenticated APIs.
    - Every authenticated user must have an AOS User Preference.
    """
    user, err = require_authenticated_user()
    if err:
        return None, err

    pref_exists = frappe.db.exists("AOS User Preference", {"user": user})
    if not pref_exists:
        return None, fail(
            "User preference not configured.",
            error="PREFERENCE_MISSING",
            http_status=403,
        )

    return user, None
