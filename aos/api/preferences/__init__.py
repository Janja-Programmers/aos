"""Preference endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .get_my_preference import get_my_preference_impl
from .update_my_preference import update_my_preference_impl


@frappe.whitelist(methods=["GET"])
def get_my_preference():
    """Fetch current user's market preferences."""
    return get_my_preference_impl()


@frappe.whitelist(methods=["POST"])
def update_my_preference(**kwargs):
    """Update current user's market preferences."""
    return update_my_preference_impl(**kwargs)
