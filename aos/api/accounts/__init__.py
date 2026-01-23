"""Accounts (Profile) endpoints.

Follows the same structure as aos.api.auth:
- whitelisted wrappers here
- implementation in sibling modules
"""

import frappe

from .profile import get_profile_impl, update_profile_impl


@frappe.whitelist(methods=["GET"])
def get_profile():
    """Return current user's profile."""
    return get_profile_impl()


@frappe.whitelist(methods=["POST"])
def update_profile(**kwargs):
    """Update allowed profile fields for current user.

    Expected kwargs (allowlisted in constants.EDITABLE_USER_FIELDS):
      - full_name
      - user_image  (file_url; upload first)
    """
    return update_profile_impl(**kwargs)
