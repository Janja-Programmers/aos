"""
Create/submit an Ad (final submit).

Strict market enforcement:
- Country is derived from location.
- Location must belong to user's market.
- Currency is enforced from user preference.
- Client cannot override market.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_days, today, getdate

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.market_context import resolve_market_country
from aos.api.shared.validators import resolve_location
from aos.utils.aos_settings import get_aos_settings_snapshot
from aos.services.account_service import get_or_create_seller
from aos.services.moderation_service import enqueue_ad_moderation

from .constants import CREATE_AD_LIMIT_PER_MINUTE_PER_USER
from .activity import record_ad_posted_activity
from .media import (
    attach_ad_media,
    get_media_public_url,
    normalize_media_id,
    validate_ad_media_for_use,
)
from .validators import (
    sanitize_details,
    sanitize_images,
    validate_basic_fields,
)

_MAX_IMAGES = 4


def _safe_float(val: Any):
    if val in (None, ""):
        return None
    try:
        return float(val)
    except Exception:
        return None


def create_ad_impl(**kwargs):
    """Create an AOS Ad with strict market enforcement."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:ads:create:user:{current_user}",
        ttl_seconds=60,
        limit=CREATE_AD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )

    if rl:
        return rl

    # Resolve market country
    market_country, err = resolve_market_country(None)

    if err:
        return err

    # Validate basic fields
    title, location, category, description, e = validate_basic_fields(
        kwargs.get("title"),
        kwargs.get("location"),
        kwargs.get("category"),
        kwargs.get("description"),
    )

    if e:
        return e

    # Resolve location
    location_name, e = resolve_location(
        location,
        country=market_country,
    )

    if e:
        return e

    location_country = frappe.db.get_value(
        "AOS Location",
        location_name,
        "country",
    )

    if not location_country or location_country != market_country:
        return fail(
            "Invalid location for your market.",
            error="VALIDATION_ERROR",
        )

    # Resolve user preference
    pref_currency = frappe.db.get_value(
        "AOS User Preference",
        {"user": current_user},
        "currency",
    )

    if not pref_currency:
        return fail(
            "User currency preference not configured.",
            error="CONFIG_ERROR",
        )

    # Resolve seller
    seller = get_or_create_seller(current_user)

    if not seller:
        return fail(
            "Unable to resolve seller account.",
            error="CONFIG_ERROR",
        )

    if seller.status != "Active":
        return fail(
            "Your seller account is currently suspended.",
            error="ACCOUNT_SUSPENDED",
        )

    # Sanitize details
    details_rows = sanitize_details(kwargs.get("details"), category=category,)

    # Sanitize images
    images_rows = sanitize_images(kwargs.get("images"))

    if len(images_rows) > _MAX_IMAGES:
        return fail(
            f"Maximum {_MAX_IMAGES} images allowed.",
            error="VALIDATION_ERROR",
        )

    if not images_rows:
        return fail("At least one image is required.", error="VALIDATION_ERROR")

    # Validate video media. New clients should pass `video_media`; `video` is
    # accepted only when it contains a media id for migration convenience.
    video_media_id = normalize_media_id(
        kwargs.get("video_media")
        or kwargs.get("video_media_id")
        or kwargs.get("video")
    )
    video_url = ""

    if video_media_id:
        video_doc, e = validate_ad_media_for_use(
            media_id=video_media_id,
            user=current_user,
            purpose="ad_video",
            kind="Video",
        )
        if e:
            return e
        video_url = get_media_public_url(video_doc.name)

    # Validate image media
    primary_count = 0
    seen_media: set[str] = set()

    for index, row in enumerate(images_rows, start=1):
        media_id = normalize_media_id(row.get("media") or row.get("media_id"))

        if not media_id:
            return fail(f"Image media id is required on row {index}.", error="VALIDATION_ERROR")

        if media_id in seen_media:
            return fail("Duplicate image selected.", error="VALIDATION_ERROR")

        media_doc, e = validate_ad_media_for_use(
            media_id=media_id,
            user=current_user,
            purpose="ad_image",
            kind="Image",
        )

        if e:
            return e

        seen_media.add(media_id)
        row["media"] = media_doc.name
        row["media_id"] = media_doc.name
        row["image"] = get_media_public_url(media_doc.name)
        row["is_primary"] = int(row.get("is_primary") or 0)

        if row["is_primary"] == 1:
            primary_count += 1

    if primary_count != 1:
        return fail("Exactly one primary image is required.", error="VALIDATION_ERROR")

    # Pricing
    price_type = kwargs.get("price_type")
    price = _safe_float(kwargs.get("price"))
    price_unit = kwargs.get("price_unit")

    # Offer fields
    offer_price = _safe_float(kwargs.get("offer_price"))
    offer_start_date = kwargs.get("offer_start_date")
    offer_end_date = kwargs.get("offer_end_date")

    if offer_start_date:
        try:
            offer_start_date = getdate(offer_start_date)
        except Exception:
            return fail(
                "Invalid offer_start_date.",
                error="VALIDATION_ERROR",
            )

    if offer_end_date:
        try:
            offer_end_date = getdate(offer_end_date)
        except Exception:
            return fail(
                "Invalid offer_end_date.",
                error="VALIDATION_ERROR",
            )

    if offer_price is not None and offer_price <= 0:
        return fail(
            "offer_price must be greater than 0.",
            error="VALIDATION_ERROR",
        )

    if offer_start_date and offer_end_date:
        if offer_start_date > offer_end_date:
            return fail(
                "offer_start_date cannot be greater than offer_end_date.",
                error="VALIDATION_ERROR",
            )

    # Create Ad
    try:
        ad = frappe.new_doc("AOS Ad")
        ad.title = title
        ad.location = location_name
        ad.country = location_country
        ad.category = category
        ad.description = description
        ad.status = "Reviewing"
        ad.seller = seller.name

        # Expiry
        settings = get_aos_settings_snapshot()

        ad.expires_on = add_days(
            today(),
            settings.ad_expiry_days,
        )

        # Pricing
        ad.currency = pref_currency

        if price_type:
            ad.price_type = price_type

        if price is not None:
            ad.price = price

        if price_unit:
            ad.price_unit = price_unit

        # Offers
        if offer_price is not None:
            ad.offer_price = offer_price

        if offer_start_date:
            ad.offer_start_date = offer_start_date

        if offer_end_date:
            ad.offer_end_date = offer_end_date

        # Video
        if video_media_id:
            ad.video_media = video_media_id
            ad.video = video_url

        # Details
        for row in details_rows:
            child = ad.append("details", {})

            for k, v in row.items():
                setattr(child, k, v)

        # Images
        for row in images_rows:
            child = ad.append("images", {})

            child.media = row.get("media")
            child.image = row.get("image")
            child.is_primary = row.get("is_primary")

            if row.get("sort_order") is not None:
                child.sort_order = row.get("sort_order")

        # Insert
        ad.insert(ignore_permissions=True)

        # Attach MinIO media metadata to this ad.
        for row in images_rows:
            _media_doc, attach_error = attach_ad_media(
                media_id=row.get("media"),
                user=current_user,
                purpose="ad_image",
                ad_name=ad.name,
                attached_field="images",
            )
            if attach_error:
                frappe.db.rollback()
                return attach_error

        if video_media_id:
            _media_doc, attach_error = attach_ad_media(
                media_id=video_media_id,
                user=current_user,
                purpose="ad_video",
                ad_name=ad.name,
                attached_field="video_media",
            )
            if attach_error:
                frappe.db.rollback()
                return attach_error

        moderation_job = enqueue_ad_moderation(ad.name, source="ad_create")

        record_ad_posted_activity(
            user=current_user,
            ad_id=ad.name,
        )

        return ok(
            "Ad created and queued for moderation.",
            data={
                "id": ad.name,
                "status": ad.status,
                "moderation_job_id": getattr(moderation_job, "name", None),
                "moderation_job_status": getattr(moderation_job, "status", None),
            },
        )

    except frappe.ValidationError as ex:

        frappe.db.rollback()

        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:

        frappe.log_error(
            frappe.get_traceback(),
            "AOS Create Ad Failed",
        )

        frappe.db.rollback()

        return fail(
            "Failed to create ad.",
            error="INTERNAL_ERROR",
        )
