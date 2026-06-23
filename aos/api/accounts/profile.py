"""Profile (User) implementation.

These functions contain the business logic. Public whitelisted wrappers live in
accounts/__init__.py to match the existing auth structure.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.blocking import get_block_status
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import EDITABLE_USER_FIELDS
from .constants import (
    GET_PROFILE_LIMIT_PER_MINUTE_PER_USER,
    UPDATE_PROFILE_LIMIT_PER_MINUTE_PER_USER,
)

from .serializers import serialize_user

from .validators import (
    validate_full_name,
    validate_bio,
    validate_user_image,
    attach_file_to_user,
)


def get_profile_impl(**kwargs):
    """
    Fetch a user profile.

    Behavior:
      - If no target user is provided, fetch current logged-in user's profile.
      - If target_user is provided, fetch that user's public profile.
    """

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:accounts:get_profile:user:{current_user}",
        ttl_seconds=60,
        limit=GET_PROFILE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )

    if rl:
        return rl

    target_user = kwargs.get("target_user") or current_user

    try:
        if not frappe.db.exists("User", target_user):
            return fail("User not found.", code="NOT_FOUND")

        if not frappe.db.exists("AOS Profile", target_user):
            return fail("User profile not found.", code="PROFILE_NOT_FOUND")

        if target_user != current_user:
            block = get_block_status(
                current_user=current_user,
                target_user=target_user,
            )
            if block.get("has_blocked_me"):
                return fail(
                    "Profile is unavailable.",
                    code="PROFILE_UNAVAILABLE",
                    data=block,
                    http_status=403,
                )

        user_doc = frappe.get_doc("User", target_user)

        return ok(
            "Profile fetched.",
            data=serialize_user(
                user_doc,
                current_user=current_user,
            ),
        )

    except frappe.DoesNotExistError:
        return fail("User not found.", code="NOT_FOUND")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Profile Failed",
        )

        return fail(
            "Failed to fetch profile.",
            code="INTERNAL_ERROR",
        )


def update_profile_impl(**kwargs):
    """Update editable user profile fields for the current logged-in user only."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:accounts:update_profile:user:{current_user}",
        ttl_seconds=60,
        limit=UPDATE_PROFILE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )

    if rl:
        return rl

    incoming = {
        k: v for k, v in (kwargs or {}).items() if k in EDITABLE_USER_FIELDS
    }

    if not incoming:
        return fail(
            "No editable fields provided.",
            code="VALIDATION_ERROR",
        )

    try:
        user_doc = frappe.get_doc("User", current_user)

        # Update full name
        if "full_name" in incoming:
            full_name, e = validate_full_name(incoming.get("full_name"))
            if e:
                return e

            user_doc.first_name = full_name

        # Update bio
        if "bio" in incoming:
            bio, e = validate_bio(incoming.get("bio"))
            if e:
                return e

            user_doc.bio = bio

        # Update user image
        if "user_image" in incoming:
            file_url, e = validate_user_image(
                incoming.get("user_image"),
                current_user=current_user,
            )

            if e:
                return e

            attach_file_to_user(
                file_url,
                current_user=current_user,
            )

            user_doc.user_image = file_url or ""

        user_doc.save(ignore_permissions=True)

        frappe.db.commit()

        return ok(
            "Profile updated.",
            data=serialize_user(
                user_doc,
                current_user=current_user,
            ),
        )

    except frappe.DoesNotExistError:
        return fail("User not found.", code="NOT_FOUND")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Update Profile Failed",
        )

        return fail(
            "Failed to update profile.",
            code="INTERNAL_ERROR",
        )
