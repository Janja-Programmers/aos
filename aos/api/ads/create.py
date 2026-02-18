"""Create/submit an Ad (final submit).

This endpoint is intended to be called after the client has already:
  1) Selected category
  2) Fetched schema (details + pricing)
  3) Uploaded images/video via /api/method/upload_file
  4) Collected form values

Server-side validations happen inside the AOS Ad DocType controller (validate()).
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_days, today, getdate

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.utils.aos_settings import get_aos_settings_snapshot

from .constants import CREATE_AD_LIMIT_PER_MINUTE_PER_USER
from .validators import (
    attach_file_to_ad,
    sanitize_details,
    sanitize_images,
    validate_basic_fields,
    validate_file_reference,
)


def _safe_float(val: Any):
    if val in (None, ""):
        return None
    try:
        return float(val)
    except Exception:
        return None


def create_ad_impl(**kwargs):
    """Create an AOS Ad with details, pricing, offers and media."""

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

    title, location, category, description, e = validate_basic_fields(
        kwargs.get("title"),
        kwargs.get("location"),
        kwargs.get("category"),
        kwargs.get("description"),
    )
    if e:
        return e

    details_rows = sanitize_details(kwargs.get("details"))
    images_rows = sanitize_images(kwargs.get("images"))

    # Media
    video_url, e = validate_file_reference(
        kwargs.get("video"),
        current_user=current_user,
        kind="Video",
    )
    if e:
        return e

    for row in images_rows:
        img_url, e = validate_file_reference(
            row.get("image"),
            current_user=current_user,
            kind="Image",
        )
        if e:
            return e
        row["image"] = img_url

    # Pricing
    price_type = kwargs.get("price_type")
    currency = kwargs.get("currency")
    price = kwargs.get("price")
    price_unit = kwargs.get("price_unit")

    # Offers
    offer_price = _safe_float(kwargs.get("offer_price"))
    offer_start_date = kwargs.get("offer_start_date")
    offer_end_date = kwargs.get("offer_end_date")

    if offer_start_date:
        try:
            offer_start_date = getdate(offer_start_date)
        except Exception:
            return fail("Invalid offer_start_date.", code="VALIDATION_ERROR")

    if offer_end_date:
        try:
            offer_end_date = getdate(offer_end_date)
        except Exception:
            return fail("Invalid offer_end_date.", code="VALIDATION_ERROR")

    # Light API-level validation (deep validation happens in DocType)
    if offer_price is not None and offer_price <= 0:
        return fail("offer_price must be greater than 0.", code="VALIDATION_ERROR")

    if offer_start_date and offer_end_date:
        if offer_start_date > offer_end_date:
            return fail(
                "offer_start_date cannot be greater than offer_end_date.",
                code="VALIDATION_ERROR",
            )

    try:
        ad = frappe.new_doc("AOS Ad")
        ad.title = title
        ad.location = location
        ad.category = category
        ad.user = current_user
        ad.description = description
        ad.status = "Reviewing"

        # Expiry
        settings = get_aos_settings_snapshot()
        ad.expires_on = add_days(today(), settings.ad_expiry_days)

        # Pricing
        if currency:
            ad.currency = currency
        if price_type:
            ad.price_type = price_type
        if price not in (None, ""):
            ad.price = price
        if price_unit:
            ad.price_unit = price_unit

        # Offer (optional)
        if offer_price is not None:
            ad.offer_price = offer_price

        if offer_start_date:
            ad.offer_start_date = offer_start_date

        if offer_end_date:
            ad.offer_end_date = offer_end_date

        # Media
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
            child.is_primary = int(row.get("is_primary") or 0)
            if row.get("sort_order") not in (None, ""):
                child.sort_order = row.get("sort_order")

        # Insert → triggers AOSAd.validate()
        ad.insert(ignore_permissions=True)

        # Attach files
        for row in images_rows:
            attach_file_to_ad(row.get("image") or "", ad_name=ad.name)
        if video_url:
            attach_file_to_ad(video_url, ad_name=ad.name)

        frappe.db.commit()

        return ok("Ad created.", data={"id": ad.name})

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Create Ad Failed")
        return fail("Failed to create ad.", code="INTERNAL_ERROR")
