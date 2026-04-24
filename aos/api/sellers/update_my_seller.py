"""
Update Seller Profile.

Allows sellers to update their shop information.
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
            {"file_url": file_url}
        )
    )


def update_my_seller_impl(**kwargs):
    """Update seller profile."""

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
            "name"
        )

        if not seller:
            return fail(
                "Seller profile not found.",
                code="NOT_FOUND"
            )

        seller_doc = frappe.get_doc("AOS Seller", seller)

        if seller_doc.status != "Active":
            return fail(
                "Seller profile is not available.",
                code="VALIDATION_ERROR"
            )

        identity_changed = False

        # Update fields
        if "shop_name" in kwargs:
            new_name = kwargs.get("shop_name")

            if new_name and new_name != seller_doc.shop_name:
                seller_doc.shop_name = new_name
                identity_changed = True

        if "about_shop" in kwargs:
            seller_doc.about_shop = kwargs.get("about_shop")

        if "physical_address" in kwargs:
            new_address = kwargs.get("physical_address")

            if new_address and new_address != seller_doc.physical_address:
                seller_doc.physical_address = new_address
                identity_changed = True

        if "avatar" in kwargs:
            avatar = kwargs.get("avatar")

            if avatar and not _file_exists(avatar):
                return fail(
                    "Avatar file does not exist.",
                    code="VALIDATION_ERROR"
                )

            seller_doc.avatar = avatar

        if "shop_banner" in kwargs:
            banner = kwargs.get("shop_banner")

            if banner and not _file_exists(banner):
                return fail(
                    "Shop banner file does not exist.",
                    code="VALIDATION_ERROR"
                )

            seller_doc.shop_banner = banner

        # UPDATE OPERATING HOURS
        if "operating_hours" in kwargs:
            seller_doc.set("operating_hours", kwargs.get("operating_hours"))

        # REVOKE VERIFICATION IF IDENTITY CHANGED
        if identity_changed and seller_doc.is_verified:
            seller_doc.is_verified = 0
            seller_doc.seller_type = "Individual"

        seller_doc.save(ignore_permissions=True)
        frappe.db.commit()

        return ok(
            "Seller profile updated successfully.",
            data={
                "shop_name": seller_doc.shop_name,
                "about_shop": seller_doc.about_shop,
                "physical_address": seller_doc.physical_address,
                "avatar": seller_doc.avatar,
                "shop_banner": seller_doc.shop_banner,
                "is_verified": seller_doc.is_verified,
                "seller_type": seller_doc.seller_type,
            }
        )

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Update Seller Failed"
        )
        return fail(
            "Failed to update seller profile.",
            code="INTERNAL_ERROR"
        )
