"""Shared user display helpers.

These helpers keep historical records stable while respecting recoverable
account deletion.

Important:
- User IDs/emails remain stable internal identifiers.
- Public display fields are masked for soft-deleted accounts.
- Restored accounts automatically show their normal profile again because the
  underlying User data is preserved during recoverable deletion.
"""

from __future__ import annotations

from typing import Any, Iterable

import frappe

DELETED_USER_DISPLAY_NAME = "Deleted User"


def _has_profile_field(fieldname: str) -> bool:
    try:
        return bool(frappe.get_meta("AOS Profile").has_field(fieldname))
    except Exception:
        return False


def _profile_status_fields() -> list[str]:
    fields = ["user"]

    for fieldname in ("account_status", "is_deleted"):
        if _has_profile_field(fieldname):
            fields.append(fieldname)

    return fields


def _is_deleted_from_profile(profile: Any | None) -> bool:
    if not profile:
        return False

    status = (profile.get("account_status") if isinstance(profile, dict) else getattr(profile, "account_status", None))
    is_deleted = (profile.get("is_deleted") if isinstance(profile, dict) else getattr(profile, "is_deleted", 0))

    return bool(int(is_deleted or 0)) or status == "Deleted"


def _deleted_payload(user: str | None) -> dict[str, Any]:
    return {
        "user": user,
        "display_name": DELETED_USER_DISPLAY_NAME,
        "full_name": DELETED_USER_DISPLAY_NAME,
        "avatar": None,
        "user_image": None,
        "is_deleted": True,
    }


def normalize_user_display(
    *,
    user: str | None,
    full_name: str | None = None,
    avatar: str | None = None,
    is_deleted: bool = False,
    fallback_to_user: bool = True,
) -> dict[str, Any]:
    """Normalize user display fields from already-fetched data."""
    if not user:
        return {
            "user": None,
            "display_name": None,
            "full_name": None,
            "avatar": None,
            "user_image": None,
            "is_deleted": False,
        }

    if is_deleted:
        return _deleted_payload(user)

    display_name = (full_name or "").strip()
    if not display_name and fallback_to_user:
        display_name = user

    return {
        "user": user,
        "display_name": display_name or None,
        "full_name": display_name or None,
        "avatar": avatar,
        "user_image": avatar,
        "is_deleted": False,
    }


def get_user_display(user: str | None) -> dict[str, Any]:
    """Return a display-safe payload for one User."""
    if not user:
        return normalize_user_display(user=None)

    user_row = frappe.db.get_value(
        "User",
        user,
        ["name", "full_name", "first_name", "user_image"],
        as_dict=True,
    )

    if not user_row:
        return normalize_user_display(user=user)

    profile_fields = _profile_status_fields()
    profile = frappe.db.get_value(
        "AOS Profile",
        user,
        profile_fields,
        as_dict=True,
    ) if profile_fields else None

    is_deleted = _is_deleted_from_profile(profile)

    return normalize_user_display(
        user=user_row.name,
        full_name=user_row.full_name or user_row.first_name,
        avatar=user_row.user_image,
        is_deleted=is_deleted,
    )


def get_user_display_map(users: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Batch-return display-safe payloads keyed by User.name."""
    unique_users = sorted({str(user).strip() for user in users if user})

    if not unique_users:
        return {}

    user_rows = frappe.get_all(
        "User",
        filters={"name": ["in", unique_users]},
        fields=["name", "full_name", "first_name", "user_image"],
    )

    users_by_name = {row.name: row for row in user_rows}

    profile_by_user: dict[str, Any] = {}
    profile_fields = _profile_status_fields()

    if profile_fields:
        profile_rows = frappe.get_all(
            "AOS Profile",
            filters={"user": ["in", unique_users]},
            fields=profile_fields,
        )
        profile_by_user = {row.user: row for row in profile_rows}

    result: dict[str, dict[str, Any]] = {}

    for user in unique_users:
        row = users_by_name.get(user)
        if not row:
            result[user] = normalize_user_display(user=user)
            continue

        profile = profile_by_user.get(user)
        result[user] = normalize_user_display(
            user=row.name,
            full_name=row.full_name or row.first_name,
            avatar=row.user_image,
            is_deleted=_is_deleted_from_profile(profile),
        )

    return result


def is_display_deleted(user: str | None) -> bool:
    return bool(get_user_display(user).get("is_deleted"))
