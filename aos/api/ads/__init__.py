"""Ads endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .create import create_ad_impl
from .get_ad import get_ad_impl
from .list_ads import list_ads_impl
from .my_ads import my_ads_impl


@frappe.whitelist(methods=["POST"])
def create_ad(**kwargs):
    """Create/submit an Ad (final submit)."""
    return create_ad_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def list_ads(**kwargs):
    """Browse ads (buyers)."""
    return list_ads_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_ad(ad_id: str):
    """Fetch a single ad."""
    return get_ad_impl(ad_id)


@frappe.whitelist()
def my_ads(**kwargs):
    """List current user's ads."""
    return my_ads_impl(**kwargs)
