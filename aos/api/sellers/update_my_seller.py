"""
Update Seller Profile.

Allows sellers to update their business information.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import UPDATE_MY_SELLER_LIMIT_PER_MINUTE_PER_USER


def _file_exists(file_url: str) -> bool:
    """Check if a file exists in the File doctype."""
    if not file_url:
        return False

    return bool(
        frappe.db.exists(
            "File",
            {"file_url": file_url},
        )
    )


def update_my_seller_impl(**kwargs):
    """Update seller business profile."""

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
                code="NOT_FOUND",
            )

        seller_doc = frappe.get_doc("AOS Seller", seller)

        if seller_doc.status != "Active":
            return fail(
                "Seller profile is not available.",
                code="VALIDATION_ERROR",
            )

        if "business_category" in kwargs:
            seller_doc.business_category = kwargs.get("business_category")

        if "about_business" in kwargs:
            seller_doc.about_business = kwargs.get("about_business")

        if "business_address" in kwargs:
            seller_doc.business_address = kwargs.get("business_address")

        if "shop_banner" in kwargs:
            banner = kwargs.get("shop_banner")

            if banner and not _file_exists(banner):
                return fail(
                    "Shop banner file does not exist.",
                    code="VALIDATION_ERROR",
                )

            seller_doc.shop_banner = banner

        if "operating_hours" in kwargs:
            seller_doc.set("operating_hours", kwargs.get("operating_hours"))

        seller_doc.save(ignore_permissions=True)
        frappe.db.commit()

        return ok(
            "Seller profile updated successfully.",
            data={
                "business_category": seller_doc.business_category,
                "about_business": seller_doc.about_business,
                "business_address": seller_doc.business_address,
                "shop_banner": seller_doc.shop_banner,
                "seller_type": seller_doc.seller_type,
            },
        )

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Update Seller Failed",
        )
        return fail(
            "Failed to update seller profile.",
            code="INTERNAL_ERROR",
        )
