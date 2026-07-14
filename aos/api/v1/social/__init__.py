"""Public AOS API v1 wrappers for social.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.social.*.
Implementation stays in aos.api.social implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.social.toggle_follow import (
    toggle_follow_impl as _toggle_follow_impl,
)
from aos.api.social.relationship import (
    get_relationship_status_impl as _get_relationship_status_impl,
)
from aos.api.social.lists import (
    get_following_impl as _get_following_impl,
    get_followers_impl as _get_followers_impl,
    get_friends_impl as _get_friends_impl,
)
from aos.api.social.search_users import (
    search_users_impl as _search_users_impl,
)
from aos.api.social.block import (
    block_user_impl as _block_user_impl,
    unblock_user_impl as _unblock_user_impl,
    get_block_status_impl as _get_block_status_impl,
    list_blocked_users_impl as _list_blocked_users_impl,
)

@frappe.whitelist(methods=["POST"])
def toggle_follow(**kwargs):
    """Follow / Unfollow a user profile."""
    return _toggle_follow_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def get_relationship_status(**kwargs):
    """Get relationship status between current user and target user."""
    return _get_relationship_status_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def get_following(**kwargs):
    """Get users the current user is following."""
    return _get_following_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def get_followers(**kwargs):
    """Get users following the current user."""
    return _get_followers_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def get_friends(**kwargs):
    """Get mutual follows for the current user."""
    return _get_friends_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def search_users(**kwargs):
    """Search active AOS users globally."""
    return _search_users_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def block_user(**kwargs):
    """Block a user."""
    return _block_user_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def unblock_user(**kwargs):
    """Unblock a user."""
    return _unblock_user_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def get_block_status(**kwargs):
    """Get block status between current user and target user."""
    return _get_block_status_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def list_blocked_users(**kwargs):
    """List users blocked by current user."""
    return _list_blocked_users_impl(**kwargs)
