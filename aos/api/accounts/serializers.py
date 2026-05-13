"""Serializers for accounts/profile."""

from __future__ import annotations

from typing import Any


import frappe
from aos.api.social.relationship import build_relationship_status


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

    return {
        "user": target_user,
        "full_name": user_doc.full_name or user_doc.first_name or target_user,
        "email": user_doc.email,
        "user_image": user_doc.user_image,
        "total_followers": int(profile.total_followers or 0) if profile else 0,
        "total_following": int(profile.total_following or 0) if profile else 0,
        "is_verified": bool(profile.is_verified) if profile else False,
        "verified_by": profile.verified_by if profile else None,
        "verified_on": profile.verified_on if profile else None,
        "can_edit": can_edit,
        **relationship,
    }
