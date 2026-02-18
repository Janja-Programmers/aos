"""Sellers endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .get_seller import get_seller_impl
from .toggle_follow import toggle_follow_impl


@frappe.whitelist(allow_guest=True)
def get_seller(**kwargs):
    """Get seller profile (used in storefront and ad detail)."""
    return get_seller_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_follow(**kwargs):
    """Follow / Unfollow a seller."""
    return toggle_follow_impl(**kwargs)
