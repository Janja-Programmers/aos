"""Serializers for accounts/profile."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.social.relationship import build_relationship_status
from aos.api.shared.user_display import get_user_display


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

    return {
        "user": target_user,
        "full_name": display.get("display_name"),
        "email": user_doc.email if can_edit and not is_deleted else None,
        "bio": "" if is_deleted else (user_doc.get("bio") or ""),
        "user_image": display.get("avatar"),
        "is_deleted": is_deleted,
        "total_followers": int(profile.total_followers or 0) if profile and not is_deleted else 0,
        "total_following": int(profile.total_following or 0) if profile and not is_deleted else 0,
        "is_verified": bool(profile.is_verified) if profile and not is_deleted else False,
        "verified_by": profile.verified_by if profile and not is_deleted else None,
        "verified_on": profile.verified_on if profile and not is_deleted else None,
        "can_edit": can_edit and not is_deleted,
        **relationship,
    }
