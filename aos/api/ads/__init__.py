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
from .my_ads import my_ads_impl
from .update import update_ad_impl
from .status import set_ad_status_impl
from .drafts import (
    abandon_ad_draft_impl,
    get_ad_draft_impl,
    list_ad_drafts_impl,
    save_ad_draft_impl,
    submit_ad_draft_impl,
)


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
def my_ads(**kwargs):
    """List current user's ads."""
    return my_ads_impl(**kwargs)


@frappe.whitelist()
def get_my_ad(**kwargs):
    """Fetch a single ad owned by the current user (seller editing)."""
    return get_my_ad_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def update_ad(**kwargs):
    """Update/edit an ad (status-aware editing rules)."""
    return update_ad_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def set_ad_status(**kwargs):
    """Change an ad's status (seller actions)."""
    return set_ad_status_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def save_ad_draft(**kwargs):
    """Create/update an Ad Draft (autosave)."""
    return save_ad_draft_impl(**kwargs)


@frappe.whitelist()
def get_ad_draft(**kwargs):
    """Fetch a single Ad Draft by id."""
    return get_ad_draft_impl(**kwargs)


@frappe.whitelist()
def list_ad_drafts(**kwargs):
    """List current user's Ad Drafts."""
    return list_ad_drafts_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def abandon_ad_draft(**kwargs):
    """Soft-delete (abandon) a Draft."""
    return abandon_ad_draft_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def submit_ad_draft(**kwargs):
    """Submit a Draft -> create real Ad (Reviewing) and mark Draft Submitted."""
    return submit_ad_draft_impl(**kwargs)
