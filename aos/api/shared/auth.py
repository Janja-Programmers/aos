"""Shared auth/session helpers for whitelisted APIs."""

from __future__ import annotations

import frappe

from .responses import fail


def current_user() -> str:
    """Return current session user or 'Guest'."""
    return getattr(frappe.session, "user", None) or "Guest"


def require_login():
    """Return (user, None) when logged in, else (None, fail...)."""
    user = current_user()
    if user == "Guest":
        return None, fail("Please login to continue.", code="UNAUTHORIZED")
    return user, None
