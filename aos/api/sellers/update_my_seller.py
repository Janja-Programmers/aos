"""
Update Seller Profile.

Allows sellers to update their general business information.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception

from .constants import UPDATE_MY_SELLER_LIMIT_PER_MINUTE_PER_USER
from .media import (
    attach_seller_banner_media,
    clear_seller_banner_media,
    looks_like_media_id,
    normalize_media_id,
)


def update_my_seller_impl(**kwargs):
    """Update the authenticated seller's general profile."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:sellers:update_my_seller:user:{current_user}",
        ttl_seconds=60,
        limit=UPDATE_MY_SELLER_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        seller = frappe.db.get_value(
            "AOS Seller",
            {"user": current_user},
            "name",
        )

        if not seller:
            return fail(
                "Seller profile not found.",
                error="NOT_FOUND",
            )

        seller_doc = frappe.get_doc(
            "AOS Seller",
            seller,
        )

        if seller_doc.status != "Active":
            return fail(
                "Seller profile is not available.",
                error="VALIDATION_ERROR",
            )

        if "business_category" in kwargs:
            seller_doc.business_category = kwargs.get(
                "business_category"
            )

        if "about_business" in kwargs:
            seller_doc.about_business = kwargs.get(
                "about_business"
            )

        banner_media_id = (
            normalize_media_id(kwargs.get("shop_banner_media"))
            or normalize_media_id(kwargs.get("banner_media"))
            or normalize_media_id(kwargs.get("media_id"))
        )

        if not banner_media_id and looks_like_media_id(kwargs.get("shop_banner")):
            banner_media_id = normalize_media_id(kwargs.get("shop_banner"))

        if banner_media_id:
            _media_doc, banner_url, e = attach_seller_banner_media(
                media_id=banner_media_id,
                user=current_user,
                seller=seller_doc.name,
            )

            if e:
                return e

            if hasattr(seller_doc, "shop_banner_media"):
                seller_doc.shop_banner_media = banner_media_id
            seller_doc.shop_banner = banner_url or ""

        elif "shop_banner" in kwargs:
            banner = str(kwargs.get("shop_banner") or "").strip()

            if banner == "":
                clear_seller_banner_media(
                    seller=seller_doc.name,
                    user=current_user,
                )
                if hasattr(seller_doc, "shop_banner_media"):
                    seller_doc.shop_banner_media = ""
                seller_doc.shop_banner = ""
            else:
                return fail(
                    "Shop banner must be uploaded using media_id with purpose=seller_banner.",
                    error="VALIDATION_ERROR",
                )

        if "operating_hours" in kwargs:
            seller_doc.set(
                "operating_hours",
                kwargs.get("operating_hours") or [],
            )

        seller_doc.save(ignore_permissions=True)
        frappe.db.commit()

        return ok(
            "Seller profile updated successfully.",
            data={
                "business_category": seller_doc.business_category,
                "about_business": seller_doc.about_business,
                "shop_banner": seller_doc.shop_banner,
                "shop_banner_media": getattr(seller_doc, "shop_banner_media", None),
                "shop_banner_media_id": getattr(seller_doc, "shop_banner_media", None),
                "seller_type": seller_doc.seller_type,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()

        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.db.rollback()

        frappe.log_error(
            frappe.get_traceback(),
            "AOS Update Seller Failed",
        )

        return fail(
            "Failed to update seller profile.",
            error="INTERNAL_ERROR",
        )
