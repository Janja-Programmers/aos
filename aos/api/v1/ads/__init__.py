"""Canonical frontend-consumed Ads API v1 boundary."""
from __future__ import annotations

import frappe

from aos.api.shared.transport import execute_endpoint
from aos.api.ads.create import create_ad_impl
from aos.api.ads.list_ads import list_ads_impl
from aos.api.ads.image_search import search_ads_by_image_impl
from aos.api.ads.get_ad import get_ad_impl
from aos.api.ads.list_my_ads import list_my_ads_impl
from aos.api.ads.get_my_ad import get_my_ad_impl
from aos.api.ads.update import update_ad_impl
from aos.api.ads.status import set_ad_status_impl
from aos.api.ads.review import review_ad_impl
from aos.api.ads.drafts import (
    upsert_ad_draft_impl, list_my_ad_drafts_impl, get_my_ad_draft_impl,
    abandon_ad_draft_impl, submit_ad_draft_impl,
)
from aos.api.search_ranking.recommendations import related_ads_impl


def _run(handler, kwargs):
    return execute_endpoint(handler, kwargs)


@frappe.whitelist(methods=["POST"])
def create_ad(**kwargs):
    return _run(create_ad_impl, kwargs)

@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_ads(**kwargs):
    return _run(list_ads_impl, kwargs)

@frappe.whitelist(allow_guest=True, methods=["POST"])
def search_ads_by_image(**kwargs):
    return _run(search_ads_by_image_impl, kwargs)

@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_ad(**kwargs):
    return _run(get_ad_impl, kwargs)

@frappe.whitelist(allow_guest=True, methods=["GET"])
def related_ads(**kwargs):
    return _run(related_ads_impl, kwargs)

@frappe.whitelist(methods=["GET"])
def list_my_ads(**kwargs):
    return _run(list_my_ads_impl, kwargs)

@frappe.whitelist(methods=["GET"])
def get_my_ad(**kwargs):
    return _run(get_my_ad_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def update_ad(**kwargs):
    return _run(update_ad_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def transition_ad(**kwargs):
    return _run(set_ad_status_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def review_ad(**kwargs):
    return _run(review_ad_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def upsert_ad_draft(**kwargs):
    return _run(upsert_ad_draft_impl, kwargs)

@frappe.whitelist(methods=["GET"])
def list_my_ad_drafts(**kwargs):
    return _run(list_my_ad_drafts_impl, kwargs)

@frappe.whitelist(methods=["GET"])
def get_my_ad_draft(**kwargs):
    return _run(get_my_ad_draft_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def abandon_ad_draft(**kwargs):
    return _run(abandon_ad_draft_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def submit_ad_draft(**kwargs):
    return _run(submit_ad_draft_impl, kwargs)
