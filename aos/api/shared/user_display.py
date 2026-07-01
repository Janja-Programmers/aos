"""Shared user display helpers.

These helpers keep historical records stable while respecting recoverable
account deletion.

Important:
- User IDs/emails remain stable internal identifiers.
- Public display fields are masked for soft-deleted accounts.
- Restored accounts automatically show their normal profile again because the
  underlying User data is preserved during recoverable deletion.
- Live state is computed from AOS Live Stream and attached to the display
  payload so avatar UIs can show a TikTok-style LIVE ring consistently.
"""

from __future__ import annotations

from typing import Any, Iterable

import frappe

from aos.api.shared.live_state import get_user_live_state, get_users_live_state

DELETED_USER_DISPLAY_NAME = "Deleted User"


def _empty_live_state() -> dict[str, Any]:
    return {
        "is_live": False,
        "live_id": None,
        "live_status": None,
        "live_title": None,
        "live_cover_image": None,
        "live_cover_media": None,
        "live_cover_media_id": None,
        "live_started_at": None,
        "live_viewer_count": 0,
    }


def _coerce_live_state(live_state: dict[str, Any] | None) -> dict[str, Any]:
    if not live_state:
        return _empty_live_state()

    base = _empty_live_state()
    base.update(live_state)
    base["is_live"] = bool(base.get("is_live"))
    base["live_viewer_count"] = int(base.get("live_viewer_count") or 0)
    return base


def _has_profile_field(fieldname: str) -> bool:
    try:
        return bool(frappe.get_meta("AOS Profile").has_field(fieldname))
    except Exception:
        return False


def _profile_status_fields() -> list[str]:
    fields = ["user"]

    for fieldname in ("account_status", "is_deleted", "profile_image_media"):
        if _has_profile_field(fieldname):
            fields.append(fieldname)

    return fields


def _is_deleted_from_profile(profile: Any | None) -> bool:
    if not profile:
        return False

    status = (
        profile.get("account_status")
        if isinstance(profile, dict)
        else getattr(profile, "account_status", None)
    )
    is_deleted = (
        profile.get("is_deleted")
        if isinstance(profile, dict)
        else getattr(profile, "is_deleted", 0)
    )

    return bool(int(is_deleted or 0)) or status == "Deleted"




def _profile_image_media_url(profile: Any | None) -> str:
    if not profile:
        return ""

    media_id = (
        profile.get("profile_image_media")
        if isinstance(profile, dict)
        else getattr(profile, "profile_image_media", None)
    )

    if not media_id:
        return ""

    try:
        from aos.api.accounts.media import get_public_media_url

        return get_public_media_url(media_id)
    except Exception:
        return ""


def _deleted_payload(user: str | None) -> dict[str, Any]:
    return {
        "user": user,
        "display_name": DELETED_USER_DISPLAY_NAME,
        "full_name": DELETED_USER_DISPLAY_NAME,
        "avatar": None,
        "user_image": None,
        "is_deleted": True,
        **_empty_live_state(),
    }


def normalize_user_display(
    *,
    user: str | None,
    full_name: str | None = None,
    avatar: str | None = None,
    is_deleted: bool = False,
    fallback_to_user: bool = True,
    live_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize user display fields from already-fetched data.

    When is_deleted=True, public identity and live state are always masked.
    """
    if not user:
        return {
            "user": None,
            "display_name": None,
            "full_name": None,
            "avatar": None,
            "user_image": None,
            "is_deleted": False,
            **_empty_live_state(),
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
        **_coerce_live_state(live_state),
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
    profile = (
        frappe.db.get_value(
            "AOS Profile",
            user,
            profile_fields,
            as_dict=True,
        )
        if profile_fields
        else None
    )

    is_deleted = _is_deleted_from_profile(profile)

    return normalize_user_display(
        user=user_row.name,
        full_name=user_row.full_name or user_row.first_name,
        avatar=user_row.user_image or _profile_image_media_url(profile),
        is_deleted=is_deleted,
        live_state=None if is_deleted else get_user_live_state(user_row.name),
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

    live_by_user = get_users_live_state(unique_users)

    result: dict[str, dict[str, Any]] = {}

    for user in unique_users:
        row = users_by_name.get(user)
        if not row:
            result[user] = normalize_user_display(user=user)
            continue

        profile = profile_by_user.get(user)
        is_deleted = _is_deleted_from_profile(profile)

        result[user] = normalize_user_display(
            user=row.name,
            full_name=row.full_name or row.first_name,
            avatar=row.user_image or _profile_image_media_url(profile),
            is_deleted=is_deleted,
            live_state=None if is_deleted else live_by_user.get(user),
        )

    return result


def is_display_deleted(user: str | None) -> bool:
    return bool(get_user_display(user).get("is_deleted"))
