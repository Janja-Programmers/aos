"""Public AOS API v1 wrappers for ads.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.ads.*.
Implementation stays in aos.api.ads implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.ads.create import (
    create_ad_impl as _create_ad_impl,
)
from aos.api.ads.list_ads import (
    list_ads_impl as _list_ads_impl,
)
from aos.api.ads.image_search import (
    search_ads_by_image_impl as _search_ads_by_image_impl,
)
from aos.api.ads.get_ad import (
    get_ad_impl as _get_ad_impl,
)
from aos.api.ads.list_my_ads import (
    list_my_ads_impl as _list_my_ads_impl,
)
from aos.api.ads.get_my_ad import (
    get_my_ad_impl as _get_my_ad_impl,
)
from aos.api.ads.update import (
    update_ad_impl as _update_ad_impl,
)
from aos.api.ads.status import (
    set_ad_status_impl as _set_ad_status_impl,
)
from aos.api.ads.drafts import (
    upsert_ad_draft_impl as _upsert_ad_draft_impl,
    list_my_ad_drafts_impl as _list_my_ad_drafts_impl,
    get_my_ad_draft_impl as _get_my_ad_draft_impl,
    abandon_ad_draft_impl as _abandon_ad_draft_impl,
    submit_ad_draft_impl as _submit_ad_draft_impl,
)


from aos.api.v1._transport import client_kwargs as _client_kwargs


@frappe.whitelist(methods=["POST"])
def create_ad(**kwargs):
    """Create/submit an Ad (final submit)."""
    return _create_ad_impl(**_client_kwargs(kwargs))


@frappe.whitelist(allow_guest=True)
def list_ads(**kwargs):
    """Browse ads (buyers)."""
    return _list_ads_impl(**_client_kwargs(kwargs))


@frappe.whitelist(allow_guest=True)
def search_ads_by_image(**kwargs):
    """Execute the v1 ads.search_ads_by_image endpoint."""
    return _search_ads_by_image_impl(**_client_kwargs(kwargs))


@frappe.whitelist(allow_guest=True)
def get_ad(**kwargs):
    """Fetch a single ad for buyers."""
    return _get_ad_impl(**_client_kwargs(kwargs))


@frappe.whitelist()
def list_my_ads(**kwargs):
    """List current user's ads."""
    return _list_my_ads_impl(**_client_kwargs(kwargs))


@frappe.whitelist()
def get_my_ad(**kwargs):
    """Fetch a single ad owned by the current user (seller editing)."""
    return _get_my_ad_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def update_ad(**kwargs):
    """Update/edit an ad."""
    return _update_ad_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def set_ad_status(**kwargs):
    """Change an ad's status (seller actions)."""
    return _set_ad_status_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def upsert_ad_draft(**kwargs):
    """Create or update an Ad Draft (autosave)."""
    return _upsert_ad_draft_impl(**_client_kwargs(kwargs))


@frappe.whitelist()
def list_my_ad_drafts(**kwargs):
    """List current user's ad drafts."""
    return _list_my_ad_drafts_impl(**_client_kwargs(kwargs))


@frappe.whitelist()
def get_my_ad_draft(**kwargs):
    """Fetch a single draft for editing."""
    return _get_my_ad_draft_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def abandon_ad_draft(**kwargs):
    """Soft-delete (abandon) a draft."""
    return _abandon_ad_draft_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def submit_ad_draft(**kwargs):
    """Submit a draft and create a real Ad."""
    return _submit_ad_draft_impl(**_client_kwargs(kwargs))
