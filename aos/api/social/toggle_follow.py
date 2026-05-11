"""
Toggle Follow on a User.

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
    """Toggle Follow / Unfollow on a user profile."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:follow:toggle:user:{current_user}",
        ttl_seconds=60,
        limit=TOGGLE_FOLLOW_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    following_user = kwargs.get("following_user") or kwargs.get("user")

    if not following_user:
        return fail("Following user is required.", code="VALIDATION_ERROR")

    if following_user == current_user:
        return fail("You cannot follow yourself.", code="VALIDATION_ERROR")

    try:
        if not frappe.db.exists("User", following_user):
            return fail("User not found.", code="NOT_FOUND")

        if not frappe.db.exists("AOS Profile", current_user):
            return fail("Current user profile not found.", code="PROFILE_NOT_FOUND")

        if not frappe.db.exists("AOS Profile", following_user):
            return fail("User profile not found.", code="PROFILE_NOT_FOUND")

        existing = frappe.get_all(
            "AOS Follow",
            filters={
                "following_user": following_user,
                "follower_user": current_user,
            },
            fields=["name"],
            limit=1,
        )

        if not existing:
            doc = frappe.new_doc("AOS Follow")
            doc.following_user = following_user
            doc.follower_user = current_user
            doc.insert(ignore_permissions=True)
            frappe.db.commit()

            NotificationService.notify_follow(
                user=following_user,
                follower=current_user,
            )

            return ok(
                "Followed successfully.",
                data={
                    "status": "followed",
                    "is_following": True,
                    "following_user": following_user,
                },
            )

        frappe.delete_doc(
            "AOS Follow",
            existing[0].name,
            ignore_permissions=True,
        )
        frappe.db.commit()

        return ok(
            "Unfollowed successfully.",
            data={
                "status": "unfollowed",
                "is_following": False,
                "following_user": following_user,
            },
        )

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Toggle Follow Failed")
        frappe.db.rollback()
        return fail("Failed to toggle follow.", code="INTERNAL_ERROR")
