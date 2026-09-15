"""Create and submit an Ad through the canonical Ads aggregate boundary."""
from __future__ import annotations

import hashlib

import frappe
from frappe.utils import add_days, today

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import resolve_location
from aos.services.ads.api import run_ads_api
from aos.services.ads.constants import STATUS_REVIEWING
from aos.services.ads.errors import AdsPermissionError, AdsValidationError
from aos.services.ads.media import attach_all, prepare_image_rows, prepare_video
from aos.services.ads.mutations import apply_ad_values
from aos.services.ads.observability import ads_log
from aos.services.ads.validation import normalize_full_ad_payload, normalize_text
from aos.services.moderation_service import enqueue_ad_moderation
from aos.services.localization.preferences import get_user_preference
from aos.services.sellers.policy import get_or_create_seller
from aos.utils.aos_settings import get_aos_settings_snapshot

from .activity import record_ad_posted_activity
from .constants import CREATE_AD_LIMIT_PER_MINUTE_PER_USER


def _submission_hash(user: str, key: str) -> str:
    return hashlib.sha256(f"{user}\0{key}".encode("utf-8")).hexdigest()


def create_ad_impl(**kwargs):
    current_user, error = require_login()
    if error:
        return error
    limited = rate_limit(key=f"aos:ads:create:user:{current_user}", ttl_seconds=60, limit=CREATE_AD_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")
    if limited:
        return limited

    def _create():
        idempotency_key = normalize_text(kwargs.get("idempotency_key"), field="idempotency_key", max_length=128, min_length=16, required=True)
        values = normalize_full_ad_payload(kwargs)
        preference = get_user_preference(current_user, use_cache=False)
        if not preference or not preference.get("country") or not preference.get("currency"):
            return fail("User market preference is not configured.", error="CONFIG_ERROR")
        market_country = str(preference.country)
        location_name, location_error = resolve_location(values["location"], country=market_country)
        if location_error:
            return location_error
        location_country = frappe.db.get_value("AOS Location", location_name, "country")
        if not location_country or location_country != market_country:
            return fail("Invalid location for your market.", error="INVALID_LOCATION")

        seller = get_or_create_seller(current_user)
        if not seller or seller.status != "Active":
            raise AdsPermissionError("Seller account is not active.", code="AD_SELLER_INACTIVE")
        key_hash = _submission_hash(current_user, idempotency_key)
        existing = frappe.db.get_value("AOS Ad", {"submission_key_hash": key_hash}, ["public_id","status","modified"], as_dict=True)
        if existing:
            return ok("Ad submission already accepted.", data={"id":existing.public_id,"status":existing.status,"version":str(existing.modified),"idempotent_replay":True})

        image_rows = prepare_image_rows(values["images"], user=current_user)
        video_id = prepare_video(values.get("video_media"), user=current_user)
        values.update({"country":location_country,"location":location_name,"currency":preference.currency,"seller":seller.name,"images":image_rows,"video_media":video_id})

        ad = frappe.new_doc("AOS Ad")
        ad.flags.aos_status_action = "create"
        ad.status = STATUS_REVIEWING
        ad.submission_key_hash = key_hash
        ad.expires_on = add_days(today(), get_aos_settings_snapshot().ad_expiry_days)
        apply_ad_values(ad, values, include_market=True)
        try:
            ad.insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            replay = frappe.db.get_value("AOS Ad", {"submission_key_hash": key_hash}, ["public_id","status","modified"], as_dict=True)
            if replay:
                return ok("Ad submission already accepted.", data={"id":replay.public_id,"status":replay.status,"version":str(replay.modified),"idempotent_replay":True})
            raise

        attach_all(user=current_user, ad_name=ad.name, image_ids=[row["media"] for row in image_rows], video_id=video_id)
        moderation_job = enqueue_ad_moderation(ad.name, source="ad_create")
        record_ad_posted_activity(user=current_user, ad_id=ad.name)
        ads_log("created", status=ad.status, outcome="success")
        return ok("Ad created and queued for review.", data={
            "id":ad.public_id,"status":ad.status,"version":str(ad.modified),
            "review_job_queued": bool(getattr(moderation_job,"name",None)),
        })

    return run_ads_api(_create, fallback="Failed to create ad.", log_title="AOS Create Ad Failed")
