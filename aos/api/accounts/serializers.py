"""Serializers for accounts/profile."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.social.relationship import build_relationship_status
from aos.api.shared.formatters import humanize_count, to_non_negative_int
from aos.api.shared.user_display import get_user_display

from .profile_stats import get_user_short_likes_count


def serialize_user(user_doc, *, current_user: str | None = None) -> dict[str, Any]:
    """
    Return a stable, mobile-friendly user profile payload.

    If current_user is provided, this behaves like get_seller:
      - current_user == user_doc.name => can_edit=True, action_label="You"
      - otherwise relationship state controls Follow/Following/Follow Back/Friends
    """

    target_user = user_doc.name
    viewer = current_user or target_user

    profile = frappe.db.get_value(
        "AOS Profile",
        target_user,
        [
            "total_followers",
            "total_following",
            "is_verified",
            "verified_by",
            "verified_on",
        ],
        as_dict=True,
    )

    relationship = build_relationship_status(
        current_user=viewer,
        target_user=target_user,
    )

    can_edit = viewer == target_user
    display = get_user_display(target_user)
    is_deleted = bool(display.get("is_deleted"))

    total_followers = (
        to_non_negative_int(profile.total_followers)
        if profile and not is_deleted
        else 0
    )
    total_following = (
        to_non_negative_int(profile.total_following)
        if profile and not is_deleted
        else 0
    )
    total_friends = 0 if is_deleted else _get_total_friends(target_user)
    total_short_likes = 0 if is_deleted else get_user_short_likes_count(target_user)
    live_viewer_count = (
        to_non_negative_int(display.get("live_viewer_count"))
        if not is_deleted
        else 0
    )

    return {
        "user": target_user,
        "full_name": display.get("display_name"),
        "email": user_doc.email if can_edit and not is_deleted else None,
        "bio": "" if is_deleted else (user_doc.get("bio") or ""),
        "user_image": display.get("avatar"),
        "is_deleted": is_deleted,
        "is_live": bool(display.get("is_live")) if not is_deleted else False,
        "live_id": display.get("live_id") if not is_deleted else None,
        "live_status": display.get("live_status") if not is_deleted else None,
        "live_title": display.get("live_title") if not is_deleted else None,
        "live_cover_image": display.get("live_cover_image") if not is_deleted else None,
        "live_started_at": display.get("live_started_at") if not is_deleted else None,
        "live_viewer_count": live_viewer_count,
        "live_viewer_count_display": humanize_count(live_viewer_count),
        "total_followers": total_followers,
        "total_followers_display": humanize_count(total_followers),
        "total_following": total_following,
        "total_following_display": humanize_count(total_following),
        "total_friends": total_friends,
        "total_friends_display": humanize_count(total_friends),
        "total_short_likes": total_short_likes,
        "total_short_likes_display": humanize_count(total_short_likes),
        "is_verified": bool(profile.is_verified) if profile and not is_deleted else False,
        "verified_by": profile.verified_by if profile and not is_deleted else None,
        "verified_on": profile.verified_on if profile and not is_deleted else None,
        "can_edit": can_edit and not is_deleted,
        **relationship,
    }


def _get_total_friends(user: str) -> int:
    """Return number of mutual follows for a user profile."""
    if not user:
        return 0

    rows = frappe.db.sql(
        """
        SELECT COUNT(*) AS total_friends
        FROM `tabAOS Follow` outgoing_follow
        INNER JOIN `tabAOS Follow` incoming_follow
            ON incoming_follow.follower_user = outgoing_follow.following_user
           AND incoming_follow.following_user = outgoing_follow.follower_user
        WHERE outgoing_follow.follower_user = %s
        """,
        (user,),
        as_dict=True,
    )

    return to_non_negative_int(rows[0].get("total_friends") if rows else 0)
