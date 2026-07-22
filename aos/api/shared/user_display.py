"""Privacy-safe shared public identity primitives."""

from __future__ import annotations

import re
from typing import Any, Iterable

import frappe

from aos.api.shared.live_state import get_user_live_state, get_users_live_state
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.accounts.serializers import serialize_internal_identity

DELETED_USER_DISPLAY_NAME = "Deleted User"
DEACTIVATED_USER_DISPLAY_NAME = "Unavailable User"
_EMAIL_LIKE_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


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


def _safe_name(value: str | None, *, fallback: str = "AOS User") -> str:
    text = str(value or "").strip()
    if not text or _EMAIL_LIKE_RE.fullmatch(text):
        return fallback
    return text


def normalize_user_display(
    *,
    user: str | None,
    full_name: str | None = None,
    avatar: str | None = None,
    is_deleted: bool = False,
    is_deactivated: bool = False,
    fallback_to_user: bool = False,
    live_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not user:
        return {
            "account_id": None,
            "user": None,
            "display_name": None,
            "full_name": None,
            "avatar": None,
            "user_image": None,
            "is_deleted": False,
            "is_deactivated": False,
            **_empty_live_state(),
        }
    public_id = public_account_id_for_user(user)
    if is_deleted:
        name, avatar, live_state = DELETED_USER_DISPLAY_NAME, None, None
    elif is_deactivated:
        name, avatar, live_state = DEACTIVATED_USER_DISPLAY_NAME, None, None
    else:
        name = _safe_name(full_name, fallback="AOS User")
        if fallback_to_user and name == "AOS User":
            name = _safe_name(user, fallback="AOS User")
    live = _empty_live_state()
    if live_state and not (is_deleted or is_deactivated):
        live.update(live_state)
        live["is_live"] = bool(live.get("is_live"))
        live["live_viewer_count"] = int(live.get("live_viewer_count") or 0)
    return {
        "account_id": public_id,
        "user": public_id,
        "display_name": name,
        "full_name": name,
        "avatar": avatar,
        "user_image": avatar,
        "is_deleted": bool(is_deleted),
        "is_deactivated": bool(is_deactivated),
        **live,
    }


def get_user_display(user: str | None) -> dict[str, Any]:
    if not user:
        return normalize_user_display(user=None)
    identity = serialize_internal_identity(str(user))
    live = None
    if not identity["is_deleted"] and not identity["is_deactivated"]:
        live = get_user_live_state(str(user))
    return normalize_user_display(
        user=str(user),
        full_name=identity.get("display_name"),
        avatar=identity.get("avatar"),
        is_deleted=bool(identity.get("is_deleted")),
        is_deactivated=bool(identity.get("is_deactivated")),
        live_state=live,
    )


def get_user_display_map(users: Iterable[str]) -> dict[str, dict[str, Any]]:
    unique = sorted({str(user).strip() for user in users if user})
    if not unique:
        return {}
    live_by_user = get_users_live_state(unique)
    result: dict[str, dict[str, Any]] = {}
    for user in unique:
        identity = serialize_internal_identity(user)
        result[user] = normalize_user_display(
            user=user,
            full_name=identity.get("display_name"),
            avatar=identity.get("avatar"),
            is_deleted=bool(identity.get("is_deleted")),
            is_deactivated=bool(identity.get("is_deactivated")),
            live_state=live_by_user.get(user),
        )
    return result


def is_display_deleted(user: str | None) -> bool:
    return bool(get_user_display(user).get("is_deleted"))
