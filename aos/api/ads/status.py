"""Seller-owned canonical Ads lifecycle actions."""
from __future__ import annotations

import frappe
from frappe.utils import add_days, getdate, today

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.ads.api import run_ads_api
from aos.services.ads.authorization import get_owned_ad_row, require_active_seller
from aos.services.ads.concurrency import assert_version
from aos.services.ads.constants import ACTION_DELETE, ACTION_MARK_AVAILABLE, STATUS_DELETED
from aos.services.ads.indexing import enqueue_discovery_refresh
from aos.services.ads.lifecycle import transition_for_action
from aos.services.ads.media import release_all
from aos.services.ads.mutations import apply_transition, lock_ad
from aos.services.ads.observability import ads_log
from aos.services.ads.validation import ensure_known_fields, normalize_identifier, normalize_text
from aos.services.marketplace_discovery.ids import resolve_ad_name
from aos.utils.aos_settings import get_aos_settings_snapshot

from .constants import SET_AD_STATUS_LIMIT_PER_MINUTE_PER_USER


def _media_ids(doc):
    values=[str(getattr(row,"media","") or "").strip() for row in (doc.images or [])]
    if getattr(doc,"video_media",None): values.append(str(doc.video_media).strip())
    return [value for value in values if value]


def set_ad_status_impl(**kwargs):
    user, error = require_login()
    if error: return error
    limited=rate_limit(key=f"aos:ads:status:user:{user}", ttl_seconds=60, limit=SET_AD_STATUS_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")
    if limited: return limited

    def _change():
        from aos.services.ads.constants import STATUS_FIELDS
        ensure_known_fields(kwargs, STATUS_FIELDS)
        public_id=normalize_identifier(kwargs.get("ad_id"), field="ad_id", required=True)
        action=normalize_text(kwargs.get("action"), field="action", max_length=40, required=True).lower()
        ad_name=resolve_ad_name(public_id)
        get_owned_ad_row(user, ad_name)
        if action != ACTION_DELETE: require_active_seller(user)
        lock_ad(ad_name)
        doc=frappe.get_doc("AOS Ad", ad_name)
        transition=transition_for_action(action, doc.status)
        # Repeated delivery of an already-completed action is idempotent even if
        # the caller's old version no longer matches. Real state changes require
        # the exact owner version loaded by the client.
        if transition.changed:
            assert_version(doc, kwargs.get("version"))
            apply_transition(doc, transition)
            if action == ACTION_MARK_AVAILABLE and (not doc.expires_on or getdate(doc.expires_on) < getdate(today())):
                doc.expires_on=add_days(today(), get_aos_settings_snapshot().ad_expiry_days)
            doc.save(ignore_permissions=True)
            if transition.new_status == STATUS_DELETED:
                release_all(user=user, ad_name=doc.name, media_ids=_media_ids(doc))
            enqueue_discovery_refresh(doc.name, status=doc.status, source=f"ad_{action}")
        ads_log("status_changed", status=doc.status, outcome="success")
        return ok("Ad lifecycle updated.", data={"id":doc.public_id,"status":doc.status,"version":str(doc.modified),"expires_on":doc.expires_on,"changed":transition.changed})

    return run_ads_api(_change, fallback="Failed to update ad lifecycle.", log_title="AOS Ad Transition Failed", transactional=True)
