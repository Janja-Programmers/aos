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
from aos.api.shared.market_context import resolve_market_country
from aos.api.shared.validators import resolve_location
from aos.utils.aos_settings import get_aos_settings_snapshot
from aos.services.account_service import get_or_create_seller

from .constants import CREATE_AD_LIMIT_PER_MINUTE_PER_USER
from .validators import (
    attach_file_to_ad,
    sanitize_details,
    sanitize_images,
    validate_basic_fields,
    validate_file_reference,
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
            code="VALIDATION_ERROR",
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
            code="CONFIG_ERROR",
        )

    # Resolve seller
    seller = get_or_create_seller(current_user)

    if not seller:
        return fail(
            "Unable to resolve seller account.",
            code="CONFIG_ERROR",
        )

    if seller.status != "Active":
        return fail(
            "Your seller account is currently suspended.",
            code="ACCOUNT_SUSPENDED",
        )

    # Sanitize details
    details_rows = sanitize_details(kwargs.get("details"), category=category,)

    # Sanitize images
    images_rows = sanitize_images(kwargs.get("images"))

    if len(images_rows) > _MAX_IMAGES:
        return fail(
            f"Maximum {_MAX_IMAGES} images allowed.",
            code="VALIDATION_ERROR",
        )

    # Validate video
    video_url, e = validate_file_reference(
        kwargs.get("video"),
        current_user=current_user,
        kind="Video",
    )

    if e:
        return e

    # Validate images
    for row in images_rows:
        img_url, e = validate_file_reference(
            row.get("image"),
            current_user=current_user,
            kind="Image",
        )

        if e:
            return e

        row["image"] = img_url

        # Normalize values
        row["is_primary"] = int(row.get("is_primary") or 0)

        if row.get("sort_order") not in (None, ""):
            try:
                row["sort_order"] = int(row["sort_order"])
            except Exception:
                row["sort_order"] = None

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
                code="VALIDATION_ERROR",
            )

    if offer_end_date:
        try:
            offer_end_date = getdate(offer_end_date)
        except Exception:
            return fail(
                "Invalid offer_end_date.",
                code="VALIDATION_ERROR",
            )

    if offer_price is not None and offer_price <= 0:
        return fail(
            "offer_price must be greater than 0.",
            code="VALIDATION_ERROR",
        )

    if offer_start_date and offer_end_date:
        if offer_start_date > offer_end_date:
            return fail(
                "offer_start_date cannot be greater than offer_end_date.",
                code="VALIDATION_ERROR",
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
        if video_url:
            ad.video = video_url

        # Details
        for row in details_rows:
            child = ad.append("details", {})

            for k, v in row.items():
                setattr(child, k, v)

        # Images
        for row in images_rows:
            child = ad.append("images", {})

            child.image = row.get("image")
            child.is_primary = row.get("is_primary")

            if row.get("sort_order") is not None:
                child.sort_order = row.get("sort_order")

        # Insert
        ad.insert(ignore_permissions=True)

        # Attach media
        for row in images_rows:
            attach_file_to_ad(
                row.get("image") or "",
                ad_name=ad.name,
            )

        if video_url:
            attach_file_to_ad(
                video_url,
                ad_name=ad.name,
            )

        try:
            frappe.enqueue(
                "aos.services.image_search_service.index_ad_images",
                queue="short",
                timeout=300,
                ad_id=ad.name,
                images=images_rows,
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Failed to enqueue image indexing for {ad.name}",
            )

        return ok(
            "Ad created.",
            data={"id": ad.name},
        )

    except frappe.ValidationError as ex:

        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:

        frappe.log_error(
            frappe.get_traceback(),
            "AOS Create Ad Failed",
        )

        frappe.db.rollback()

        return fail(
            "Failed to create ad.",
            code="INTERNAL_ERROR",
        )
