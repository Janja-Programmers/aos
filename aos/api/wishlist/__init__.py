"""Wishlist endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .toggle import toggle_wishlist_impl
from .list import list_wishlist_impl


@frappe.whitelist(methods=["POST"])
def toggle_wishlist(**kwargs):
    """Add or remove an Ad from the current user's wishlist."""
    return toggle_wishlist_impl(**kwargs)


@frappe.whitelist()
def list_wishlist(**kwargs):
    """List current user's wishlist (Active only)."""
    return list_wishlist_impl(**kwargs)
