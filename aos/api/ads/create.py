"""Create/submit an Ad (final submit).

This endpoint is intended to be called after the client has already:
  1) Selected category
  2) Fetched schema (details + pricing)
  3) Uploaded images/video via /api/method/upload_file
  4) Collected form values

Server-side validations happen inside the AOS Ad DocType controller (validate()).
"""

from __future__ import annotations

from typing import Any, Dict

import frappe
from frappe.utils import add_days, today

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


def create_ad_impl(**kwargs):
    """Create an AOS Ad with details, pricing and media."""

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
        kwargs.get("title"), kwargs.get("location"), kwargs.get("category"), kwargs.get("description")
    )
    if e:
        return e

    details_rows = sanitize_details(kwargs.get("details"))
    images_rows = sanitize_images(kwargs.get("images"))

    # video is optional
    video_url, e = validate_file_reference(kwargs.get("video"), current_user=current_user, kind="Video")
    if e:
        return e

    # Validate image file references early (nice API errors). Controller also validates count/primary.
    for row in images_rows:
        img_url, e = validate_file_reference(row.get("image"), current_user=current_user, kind="Image")
        if e:
            return e
        row["image"] = img_url

    # Pricing fields are category-driven; we accept what client submits.
    price_type = kwargs.get("price_type")
    currency = kwargs.get("currency")
    price = kwargs.get("price")
    price_unit = kwargs.get("price_unit")

    try:
        ad = frappe.new_doc("AOS Ad")
        ad.title = title
        ad.location = location
        ad.category = category
        ad.description = description
        ad.status = "Reviewing"

        # Expiry: configured in AOS Settings (ad_expiry_days)
        settings = get_aos_settings_snapshot()
        ad.expires_on = add_days(today(), settings.ad_expiry_days)

        # Pricing
        if currency is not None and str(currency).strip() != "":
            ad.currency = currency
        if price_type is not None:
            ad.price_type = price_type
        if price is not None and str(price).strip() != "":
            ad.price = price
        if price_unit is not None:
            ad.price_unit = price_unit

        # Media
        if video_url:
            ad.video = video_url

        # Details child table
        for row in details_rows:
            child = ad.append("details", {})
            for k, v in row.items():
                setattr(child, k, v)

        # Images child table
        for row in images_rows:
            child = ad.append("images", {})
            child.image = row.get("image")
            child.is_primary = int(row.get("is_primary") or 0)
            if "sort_order" in row and row.get("sort_order") not in (None, ""):
                child.sort_order = row.get("sort_order")

        # Insert triggers AOSAd.validate() (details/pricing/media)
        ad.insert(ignore_permissions=True)

        # Attach uploaded files to the Ad so they aren't orphaned
        for row in images_rows:
            attach_file_to_ad(row.get("image") or "", ad_name=ad.name)
        if video_url:
            attach_file_to_ad(video_url, ad_name=ad.name)

        frappe.db.commit()

        return ok("Ad created.", data={"id": ad.name})

    except frappe.ValidationError as ex:
        # Frappe raises ValidationError for frappe.throw; return clean API response.
        return fail(str(ex), code="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Create Ad Failed")
        return fail("Failed to create ad.", code="INTERNAL_ERROR")
