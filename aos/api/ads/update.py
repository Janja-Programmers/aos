"""Status-aware Ads updates with strict contracts and transactional media changes."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import resolve_location
from aos.services.ads.api import run_ads_api
from aos.services.ads.authorization import get_owned_ad_row, require_active_seller
from aos.services.ads.constants import (
    ACTIVE_UPDATE_FIELDS,
    CREATE_FIELDS,
    SELLER_BLOCKED_EDIT_STATUSES,
    SELLER_EDITABLE_FULL_STATUSES,
    STATUS_ACTIVE,
    STATUS_REVIEWING,
)
from aos.services.ads.errors import AdsConflictError
from aos.services.ads.lifecycle import validate_status_transition
from aos.services.ads.media import attach_all, prepare_image_rows, prepare_video, release_removed
from aos.services.ads.mutations import apply_ad_values, apply_transition, lock_ad
from aos.services.ads.observability import ads_log
from aos.services.ads.validation import (
    ensure_known_fields,
    normalize_active_update,
    normalize_full_ad_payload,
    normalize_identifier,
    persisted_offer_value,
)
from aos.services.moderation_service import enqueue_ad_moderation

from .constants import UPDATE_AD_LIMIT_PER_MINUTE_PER_USER


def _existing_values(doc) -> dict[str, object]:
    offer_price = persisted_offer_value(doc.price_type, doc.offer_price)
    return {
        "category": doc.category,
        "price_type": doc.price_type,
        "price": doc.price,
        "price_unit": doc.price_unit,
        "offer_price": offer_price,
        "offer_start_date": doc.offer_start_date if offer_price is not None else None,
        "offer_end_date": doc.offer_end_date if offer_price is not None else None,
    }


def _media_ids(doc) -> list[str]:
    result = [str(getattr(row, "media", "") or "").strip() for row in (doc.images or [])]
    if getattr(doc, "video_media", None):
        result.append(str(doc.video_media).strip())
    return [value for value in result if value]


def update_ad_impl(**kwargs):
    user, error = require_login()
    if error:
        return error

    limited = rate_limit(
        key=f"aos:ads:update:user:{user}",
        ttl_seconds=60,
        limit=UPDATE_AD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    def _update():
        ad_id = normalize_identifier(kwargs.get("ad_id") or kwargs.get("id"), field="ad_id", required=True)
        row = get_owned_ad_row(user, ad_id, fields=["category"])
        require_active_seller(user)
        lock_ad(ad_id)
        doc = frappe.get_doc("AOS Ad", ad_id)
        status = str(row.status or "").strip()

        if status in SELLER_BLOCKED_EDIT_STATUSES:
            raise AdsConflictError("This ad cannot be edited in its current status.")

        previous_media = _media_ids(doc)
        moderation_job = None

        if status == STATUS_ACTIVE:
            ensure_known_fields(kwargs, ACTIVE_UPDATE_FIELDS)
            updates = normalize_active_update(kwargs, existing=_existing_values(doc))
            apply_ad_values(doc, updates)
            doc.save(ignore_permissions=True)
            message = "Ad updated."
        elif status in SELLER_EDITABLE_FULL_STATUSES:
            ensure_known_fields(kwargs, CREATE_FIELDS, aliases={"ad_id", "id"})
            payload = {key: value for key, value in kwargs.items() if key in CREATE_FIELDS}
            values = normalize_full_ad_payload(payload)

            # Existing Ads retain their own persisted market regardless of the
            # seller's current browsing preference. Resubmission may change the
            # location only within the Ad's country.
            ad_country = str(doc.country or "").strip()
            location_name, location_error = resolve_location(values["location"], country=ad_country)
            if location_error:
                return location_error
            location_country = frappe.db.get_value("AOS Location", location_name, "country")
            if not location_country or str(location_country) != ad_country:
                return fail("Location does not belong to the ad market.", error="INVALID_LOCATION")

            image_rows = prepare_image_rows(values["images"], user=user, ad_name=doc.name)
            video_id, video_url = prepare_video(values.get("video_media"), user=user, ad_name=doc.name)
            values.update(
                {
                    "location": location_name,
                    "images": image_rows,
                    "video_media": video_id,
                    "video": video_url,
                }
            )
            transition = validate_status_transition(status, STATUS_REVIEWING, action="seller_resubmit")
            apply_transition(doc, transition)
            doc.decline_reason = None
            apply_ad_values(doc, values)
            doc.save(ignore_permissions=True)

            attach_all(
                user=user,
                ad_name=doc.name,
                image_ids=[entry["media"] for entry in image_rows],
                video_id=video_id,
            )
            current_media = [entry["media"] for entry in image_rows]
            if video_id:
                current_media.append(video_id)
            release_removed(
                user=user,
                ad_name=doc.name,
                previous_ids=previous_media,
                current_ids=current_media,
            )
            moderation_job = enqueue_ad_moderation(doc.name, source="ad_update_reviewing")
            message = "Ad updated and queued for moderation."
        else:
            raise AdsConflictError("This ad cannot be edited in its current status.")

        ads_log("updated", status=doc.status, outcome="success")
        return ok(
            message,
            data={
                "id": doc.name,
                "status": doc.status,
                "moderation_job_id": getattr(moderation_job, "name", None),
                "moderation_job_status": getattr(moderation_job, "status", None),
            },
        )

    response = run_ads_api(_update, fallback="Failed to update ad.", log_title="AOS Update Ad Failed")
    if not response.get("ok"):
        frappe.db.rollback()
    return response
