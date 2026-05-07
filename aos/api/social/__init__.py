"""Social API endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .toggle_follow import toggle_follow_impl


@frappe.whitelist(methods=["POST"])
def toggle_follow(**kwargs):
    """Follow / Unfollow a user profile."""
    return toggle_follow_impl(**kwargs)