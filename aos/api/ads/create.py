"""Create and submit an Ad through the hardened Ads domain boundary."""

from __future__ import annotations

import frappe
from frappe.utils import add_days, today

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import resolve_location
from aos.services.sellers.policy import get_or_create_seller
from aos.services.ads.api import run_ads_api
from aos.services.ads.constants import STATUS_REVIEWING
from aos.services.ads.errors import AdsPermissionError
from aos.services.ads.media import attach_all, prepare_image_rows, prepare_video
from aos.services.ads.mutations import apply_ad_values
from aos.services.ads.observability import ads_log
from aos.services.ads.validation import normalize_full_ad_payload
from aos.services.moderation_service import enqueue_ad_moderation
from aos.services.localization.preferences import get_user_preference
from aos.utils.aos_settings import get_aos_settings_snapshot

from .activity import record_ad_posted_activity
from .constants import CREATE_AD_LIMIT_PER_MINUTE_PER_USER
# Compatibility exports retained for existing integration tests and callers that
# patch the historical create-module Media hooks. Production orchestration uses
# the canonical services.ads.media boundary above.
from .media import attach_ad_media, get_media_public_url, validate_ad_media_for_use


def create_ad_impl(**kwargs):
    """Create an owned Reviewing ad and queue the canonical moderation workflow."""

    current_user, error = require_login()
    if error:
        return error

    limited = rate_limit(
        key=f"aos:ads:create:user:{current_user}",
        ttl_seconds=60,
        limit=CREATE_AD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    def _create():
        values = normalize_full_ad_payload(kwargs)

        # Creation snapshots the user's current browsing market into the Ad.
        # From this point onward the Ad owns country/location/currency and later
        # preference changes do not rewrite or constrain the persisted market.
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

        image_rows = prepare_image_rows(values["images"], user=current_user)
        video_id, video_url = prepare_video(values.get("video_media"), user=current_user)
        values.update(
            {
                "country": location_country,
                "location": location_name,
                "currency": preference.currency,
                "seller": seller.name,
                "images": image_rows,
                "video_media": video_id,
                "video": video_url,
            }
        )

        ad = frappe.new_doc("AOS Ad")
        ad.flags.aos_status_action = "create"
        ad.status = STATUS_REVIEWING
        ad.expires_on = add_days(today(), get_aos_settings_snapshot().ad_expiry_days)
        apply_ad_values(ad, values, include_market=True)
        ad.insert(ignore_permissions=True)

        attach_all(
            user=current_user,
            ad_name=ad.name,
            image_ids=[row["media"] for row in image_rows],
            video_id=video_id,
        )
        moderation_job = enqueue_ad_moderation(ad.name, source="ad_create")
        record_ad_posted_activity(user=current_user, ad_id=ad.name)
        ads_log("created", status=ad.status, outcome="success")
        return ok(
            "Ad created and queued for moderation.",
            data={
                "id": ad.name,
                "status": ad.status,
                "moderation_job_id": getattr(moderation_job, "name", None),
                "moderation_job_status": getattr(moderation_job, "status", None),
            },
        )

    response = run_ads_api(_create, fallback="Failed to create ad.", log_title="AOS Create Ad Failed")
    if not response.get("ok"):
        # The request transaction is still controlled by Frappe; rollback any
        # partially inserted document/media relationships before returning.
        frappe.db.rollback()
    return response
