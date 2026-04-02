"""
Toggle Follow on a Seller.

Supports:
  - Follow
  - Unfollow
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from aos.services.notification_service import NotificationService

from .constants import TOGGLE_FOLLOW_LIMIT_PER_MINUTE_PER_USER


def toggle_follow_impl(**kwargs):
    """Toggle Follow / Unfollow on a Seller."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:sellers:toggle_follow:user:{current_user}",
        ttl_seconds=60,
        limit=TOGGLE_FOLLOW_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    seller = kwargs.get("seller")

    if not seller:
        return fail("Seller is required.", code="VALIDATION_ERROR")

    # Prevent self-follow
    if seller == current_user:
        return fail("You cannot follow yourself.", code="VALIDATION_ERROR")

    try:
        seller_doc = frappe.get_doc("AOS Seller", seller)

        if seller_doc.status != "Active":
            return fail("Seller is not available.", code="VALIDATION_ERROR")

        existing = frappe.get_all(
            "AOS Seller Follow",
            filters={
                "seller": seller,
                "follower": current_user
            },
            fields=["name"],
            limit=1
        )

        # FOLLOW
        if not existing:
            doc = frappe.new_doc("AOS Seller Follow")
            doc.seller = seller
            doc.follower = current_user
            doc.insert(ignore_permissions=True)
            frappe.db.commit()

            # NOTIFY SELLER
            if seller != current_user:
                NotificationService.notify_follow(
                    user=seller,
                    actor=current_user,
                )

            return ok(
                "Followed successfully.",
                data={
                    "status": "followed",
                    "is_following": True
                }
            )

        # UNFOLLOW
        frappe.delete_doc(
            "AOS Seller Follow",
            existing[0].name,
            ignore_permissions=True
        )
        frappe.db.commit()

        return ok(
            "Unfollowed successfully.",
            data={
                "status": "unfollowed",
                "is_following": False
            }
        )

    except frappe.DoesNotExistError:
        return fail("Seller not found.", code="NOT_FOUND")

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Toggle Follow Failed")
        return fail("Failed to toggle follow.", code="INTERNAL_ERROR")
