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

        # Update fields
        if "shop_name" in kwargs:
            seller_doc.shop_name = kwargs.get("shop_name")

        if "about_shop" in kwargs:
            seller_doc.about_shop = kwargs.get("about_shop")

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

        seller_doc.save(ignore_permissions=True)
        frappe.db.commit()

        return ok(
            "Seller profile updated successfully.",
            data={
                "shop_name": seller_doc.shop_name,
                "about_shop": seller_doc.about_shop,
                "avatar": seller_doc.avatar,
                "shop_banner": seller_doc.shop_banner
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
