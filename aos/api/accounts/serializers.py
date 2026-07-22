"""Backward-compatible Accounts serializer facade."""

from __future__ import annotations

from typing import Any

from aos.services.accounts.serializers import serialize_private_profile, serialize_public_profile


def serialize_user(user_doc, *, current_user: str | None = None) -> dict[str, Any]:
    target_user = str(user_doc.name)
    if current_user == target_user:
        return serialize_private_profile(target_user)
    return serialize_public_profile(target_user)
