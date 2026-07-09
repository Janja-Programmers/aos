"""
Relationship status between current user and another user.

Supports:
  - none
  - following
  - followed_by
  - friends

Also includes user-block state:
  - is_blocked_by_me
  - has_blocked_me
  - is_blocked
  - block_status
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.blocking import get_block_status
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import GET_RELATIONSHIP_STATUS_LIMIT_PER_MINUTE_PER_USER


RELATIONSHIP_NONE = "none"
RELATIONSHIP_FOLLOWING = "following"
RELATIONSHIP_FOLLOWED_BY = "followed_by"
RELATIONSHIP_FRIENDS = "friends"


def get_relationship_status_impl(**kwargs):
    """
    Get relationship status between current logged-in user and target user.

    Meaning:
      - is_self: target_user is current_user
      - is_following: current_user follows target_user
      - is_followed_by: target_user follows current_user
      - is_friend: both users follow each other

    Block fields:
      - is_blocked_by_me: current_user blocked target_user
      - has_blocked_me: target_user blocked current_user
      - is_blocked: either side has an active block
    """

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:follow:relationship:user:{current_user}",
        ttl_seconds=60,
        limit=GET_RELATIONSHIP_STATUS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    target_user = kwargs.get("target_user")

    if not target_user:
        return fail("Target user is required.", error="VALIDATION_ERROR")

    try:
        if not frappe.db.exists("User", target_user):
            return fail("User not found.", error="NOT_FOUND")

        if not frappe.db.exists("AOS Profile", current_user):
            return fail("Current user profile not found.", error="PROFILE_NOT_FOUND")

        if not frappe.db.exists("AOS Profile", target_user):
            return fail("User profile not found.", error="PROFILE_NOT_FOUND")

        data = build_relationship_status(
            current_user=current_user,
            target_user=target_user,
        )

        return ok(
            "Relationship status fetched successfully.",
            data=data,
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Relationship Status Failed",
        )
        return fail("Failed to get relationship status.", error="INTERNAL_ERROR")


def build_relationship_status(*, current_user: str, target_user: str) -> dict:
    """
    Build relationship status between current_user and target_user.

    This helper is intentionally reusable by:
      - toggle_follow.py
      - followers list
      - following list
      - friends list
      - profile serializers
      - seller/short/live serializers
    """

    if current_user == target_user:
        return _self_relationship_payload(target_user=target_user)

    block = get_block_status(
        current_user=current_user,
        target_user=target_user,
    )

    is_following = _follow_exists(
        follower_user=current_user,
        following_user=target_user,
    )

    is_followed_by = _follow_exists(
        follower_user=target_user,
        following_user=current_user,
    )

    is_friend = is_following and is_followed_by

    relationship_status = _get_relationship_status(
        is_following=is_following,
        is_followed_by=is_followed_by,
    )

    action_label = _get_action_label(
        relationship_status=relationship_status,
        block=block,
    )

    return {
        "target_user": target_user,
        "is_self": False,
        "is_following": is_following,
        "is_followed_by": is_followed_by,
        "is_friend": is_friend,
        "relationship_status": relationship_status,
        "action_label": action_label,
        **block,
    }


def _self_relationship_payload(*, target_user: str) -> dict:
    block = get_block_status(
        current_user=target_user,
        target_user=target_user,
    )

    return {
        "target_user": target_user,
        "is_self": True,
        "is_following": False,
        "is_followed_by": False,
        "is_friend": False,
        "relationship_status": RELATIONSHIP_NONE,
        "action_label": "You",
        **block,
    }


def _follow_exists(*, follower_user: str, following_user: str) -> bool:
    return bool(
        frappe.db.exists(
            "AOS Follow",
            {
                "follower_user": follower_user,
                "following_user": following_user,
            },
        )
    )


def _get_relationship_status(*, is_following: bool, is_followed_by: bool) -> str:
    if is_following and is_followed_by:
        return RELATIONSHIP_FRIENDS

    if is_following:
        return RELATIONSHIP_FOLLOWING

    if is_followed_by:
        return RELATIONSHIP_FOLLOWED_BY

    return RELATIONSHIP_NONE


def _get_action_label(*, relationship_status: str, block: dict) -> str:
    if block.get("is_blocked_by_me"):
        return "Unblock"

    if block.get("has_blocked_me"):
        return "Unavailable"

    if relationship_status == RELATIONSHIP_FRIENDS:
        return "Friends"

    if relationship_status == RELATIONSHIP_FOLLOWING:
        return "Following"

    if relationship_status == RELATIONSHIP_FOLLOWED_BY:
        return "Follow Back"

    return "Follow"
