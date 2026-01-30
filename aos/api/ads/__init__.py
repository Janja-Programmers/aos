"""Ads endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .create import create_ad_impl


@frappe.whitelist(methods=["POST"])
def create_ad(**kwargs):
    """Create/submit an Ad (final submit)."""
    return create_ad_impl(**kwargs)
