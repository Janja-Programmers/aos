"""Sellers endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .list_sellers import list_sellers_impl
from .get_seller import get_seller_impl
from .get_my_seller_status import get_my_seller_status_impl
from .submit_verification import submit_verification_impl
from .get_my_verification import get_my_verification_impl
from .update_my_seller import update_my_seller_impl


@frappe.whitelist(allow_guest=True)
def list_sellers(**kwargs):
    """List marketplace sellers."""
    return list_sellers_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_seller(**kwargs):
    """Get seller profile (used in storefront and ad detail)."""
    return get_seller_impl(**kwargs)


@frappe.whitelist()
def get_my_seller_status(**kwargs):
    """Get current user's seller status (for UI decisions)."""
    return get_my_seller_status_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def update_my_seller(**kwargs):
    """Update logged-in seller profile."""
    return update_my_seller_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def submit_verification(**kwargs):
    """Submit or resubmit seller verification request."""
    return submit_verification_impl(**kwargs)


@frappe.whitelist()
def get_my_verification(**kwargs):
    """Get logged-in seller verification status."""
    return get_my_verification_impl(**kwargs)
