"""Ads endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .create import create_ad_impl
from .get_ad import get_ad_impl
from .get_my_ad import get_my_ad_impl
from .list_ads import list_ads_impl
from .list_my_ads import list_my_ads_impl
from .update import update_ad_impl
from .status import set_ad_status_impl

from .drafts import (
    upsert_ad_draft_impl,
    get_my_ad_draft_impl,
    list_my_ad_drafts_impl,
    abandon_ad_draft_impl,
    submit_ad_draft_impl,
)

# Ads (Buyer + Seller)
@frappe.whitelist(methods=["POST"])
def create_ad(**kwargs):
    """Create/submit an Ad (final submit)."""
    return create_ad_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def list_ads(**kwargs):
    """Browse ads (buyers)."""
    return list_ads_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_ad(**kwargs):
    """Fetch a single ad for buyers."""
    return get_ad_impl(**kwargs)


@frappe.whitelist()
def list_my_ads(**kwargs):
    """List current user's ads."""
    return list_my_ads_impl(**kwargs)


@frappe.whitelist()
def get_my_ad(**kwargs):
    """Fetch a single ad owned by the current user (seller editing)."""
    return get_my_ad_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def update_ad(**kwargs):
    """Update/edit an ad."""
    return update_ad_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def set_ad_status(**kwargs):
    """Change an ad's status (seller actions)."""
    return set_ad_status_impl(**kwargs)


# Drafts
@frappe.whitelist(methods=["POST"])
def upsert_ad_draft(**kwargs):
    """Create or update an Ad Draft (autosave)."""
    return upsert_ad_draft_impl(**kwargs)


@frappe.whitelist()
def list_my_ad_drafts(**kwargs):
    """List current user's ad drafts."""
    return list_my_ad_drafts_impl(**kwargs)


@frappe.whitelist()
def get_my_ad_draft(**kwargs):
    """Fetch a single draft for editing."""
    return get_my_ad_draft_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def abandon_ad_draft(**kwargs):
    """Soft-delete (abandon) a draft."""
    return abandon_ad_draft_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def submit_ad_draft(**kwargs):
    """Submit a draft and create a real Ad."""
    return submit_ad_draft_impl(**kwargs)
