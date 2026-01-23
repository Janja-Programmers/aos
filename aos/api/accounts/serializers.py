"""Serializers for accounts/profile."""

from __future__ import annotations

from typing import Any


def serialize_user(user_doc) -> dict[str, Any]:
    """Return a stable, mobile-friendly user profile payload."""
    return {
        "full_name": user_doc.first_name,
        "email": user_doc.email,
        "user_image": user_doc.user_image,
    }
