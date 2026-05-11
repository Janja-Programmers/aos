"""Verification endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .submit_verification import submit_verification_impl
from .get_my_verification import get_my_verification_impl


@frappe.whitelist(methods=["POST"])
def submit_verification(**kwargs):
    """Submit or resubmit an account verification request."""
    return submit_verification_impl(**kwargs)


@frappe.whitelist()
def get_my_verification(**kwargs):
    """Get logged-in user's verification status."""
    return get_my_verification_impl(**kwargs)
