"""Frappe login hook enforcing the canonical AOS Website User boundary."""

from __future__ import annotations

import frappe


def enforce_aos_website_login(login_manager) -> None:
    """Reject framework-generic login for Website Users.

    System Users keep normal Frappe Desk login. AOS Website Users must create a
    session through the versioned AOS auth endpoints so rate limits, account
    state checks, and the public session contract cannot be bypassed.
    """
    user = getattr(login_manager, "user", None)
    if not user or user == "Guest":
        return
    user_type = frappe.get_cached_value("User", user, "user_type")
    if user_type != "Website User":
        return
    if getattr(frappe.flags, "aos_auth_session_creation", False):
        return
    frappe.throw("Use the AOS Authentication API to sign in.", frappe.AuthenticationError)
