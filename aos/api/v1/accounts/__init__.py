"""Public AOS API v1 wrappers for accounts.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.accounts.*.
Implementation stays in aos.api.accounts implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.accounts.profile import (
    get_profile_impl as _get_profile_impl,
    update_profile_impl as _update_profile_impl,
)
from aos.api.accounts.get_my_preference import (
    get_my_preference_impl as _get_my_preference_impl,
)
from aos.api.accounts.update_my_preference import (
    update_my_preference_impl as _update_my_preference_impl,
)
from aos.api.accounts.lifecycle import deactivate_account_impl as _deactivate_account_impl

@frappe.whitelist()
def get_profile(**kwargs):
    """Execute the v1 accounts.get_profile endpoint."""
    return _get_profile_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def update_profile(**kwargs):
    """Execute the v1 accounts.update_profile endpoint."""
    return _update_profile_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def get_my_preference(**kwargs):
    """Execute the v1 accounts.get_my_preference endpoint."""
    return _get_my_preference_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def update_my_preference(**kwargs):
    """Execute the v1 accounts.update_my_preference endpoint."""
    return _update_my_preference_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def deactivate_account(**kwargs):
    """Deactivate the authenticated account without deleting retained content."""
    return _deactivate_account_impl(**kwargs)
