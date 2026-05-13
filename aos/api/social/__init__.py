"""Social API endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .toggle_follow import toggle_follow_impl
from .relationship import get_relationship_status_impl
from .lists import (
    get_following_impl,
    get_followers_impl,
    get_friends_impl,
)


@frappe.whitelist(methods=["POST"])
def toggle_follow(**kwargs):
    """Follow / Unfollow a user profile."""
    return toggle_follow_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def get_relationship_status(**kwargs):
    """Get relationship status between current user and target user."""
    return get_relationship_status_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def get_following(**kwargs):
    """Get users the current user is following."""
    return get_following_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def get_followers(**kwargs):
    """Get users following the current user."""
    return get_followers_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def get_friends(**kwargs):
    """Get mutual follows for the current user."""
    return get_friends_impl(**kwargs)
