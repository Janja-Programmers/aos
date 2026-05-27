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
from .relationship import build_relationship_status


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

    target_user = kwargs.get("target_user")

    if not target_user:
        return fail("Target user is required.", code="VALIDATION_ERROR")

    if target_user == current_user:
        return fail("You cannot follow yourself.", code="VALIDATION_ERROR")

    try:
        if not frappe.db.exists("User", target_user):
            return fail("User not found.", code="NOT_FOUND")

        if not frappe.db.exists("AOS Profile", current_user):
            return fail("Current user profile not found.", code="PROFILE_NOT_FOUND")

        if not frappe.db.exists("AOS Profile", target_user):
            return fail("User profile not found.", code="PROFILE_NOT_FOUND")

        existing_follow = frappe.db.get_value(
            "AOS Follow",
            {
                "follower_user": current_user,
                "following_user": target_user,
            },
            "name",
        )

        if existing_follow:
            _delete_follow_direct(
                current_user=current_user,
                target_user=target_user,
            )

            _sync_profile_totals(
                current_user=current_user,
                target_user=target_user,
            )

            frappe.db.commit()

            relationship = build_relationship_status(
                current_user=current_user,
                target_user=target_user,
            )

            return ok(
                "Unfollowed successfully.",
                data={
                    "status": "unfollowed",
                    **relationship,
                    **_get_profile_totals(
                        current_user=current_user,
                        target_user=target_user,
                    ),
                },
            )

        doc = frappe.new_doc("AOS Follow")
        doc.follower_user = current_user
        doc.following_user = target_user
        doc.insert(ignore_permissions=True)

        _sync_profile_totals(
            current_user=current_user,
            target_user=target_user,
        )

        frappe.db.commit()

        NotificationService.notify_follow(
            user=target_user,
            follower=current_user,
        )

        relationship = build_relationship_status(
            current_user=current_user,
            target_user=target_user,
        )

        return ok(
            "Followed successfully.",
            data={
                "status": "followed",
                **relationship,
                **_get_profile_totals(
                    current_user=current_user,
                    target_user=target_user,
                ),
            },
        )

    except frappe.UniqueValidationError:
        frappe.db.rollback()

        _sync_profile_totals(
            current_user=current_user,
            target_user=target_user,
        )
        frappe.db.commit()

        relationship = build_relationship_status(
            current_user=current_user,
            target_user=target_user,
        )

        return ok(
            "Already followed.",
            data={
                "status": "followed",
                **relationship,
                **_get_profile_totals(
                    current_user=current_user,
                    target_user=target_user,
                ),
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Toggle Follow Failed",
        )
        frappe.db.rollback()
        return fail("Failed to toggle follow.", code="INTERNAL_ERROR")


def _delete_follow_direct(*, current_user: str, target_user: str):
    """
    Delete follow row directly.

    Avoids frappe.delete_doc(), which can lock the document and timeout under
    rapid follow/unfollow or concurrent relationship checks.
    """

    frappe.db.sql(
        """
        DELETE FROM `tabAOS Follow`
        WHERE follower_user = %s
          AND following_user = %s
        LIMIT 1
        """,
        (current_user, target_user),
    )


def _sync_profile_totals(*, current_user: str, target_user: str):
    """
    Recalculate profile counters after follow/unfollow.

    Since unfollow uses direct SQL delete, do not rely only on DocType hooks.
    """

    target_total_followers = frappe.db.count(
        "AOS Follow",
        {
            "following_user": target_user,
        },
    )

    current_total_following = frappe.db.count(
        "AOS Follow",
        {
            "follower_user": current_user,
        },
    )

    frappe.db.set_value(
        "AOS Profile",
        target_user,
        {
            "total_followers": target_total_followers,
        },
        update_modified=False,
    )

    frappe.db.set_value(
        "AOS Profile",
        current_user,
        {
            "total_following": current_total_following,
        },
        update_modified=False,
    )


def _get_profile_totals(*, current_user: str, target_user: str) -> dict:
    target_total_followers = frappe.db.get_value(
        "AOS Profile",
        target_user,
        "total_followers",
    )

    current_total_following = frappe.db.get_value(
        "AOS Profile",
        current_user,
        "total_following",
    )

    return {
        "target_total_followers": int(target_total_followers or 0),
        "current_total_following": int(current_total_following or 0),
    }
