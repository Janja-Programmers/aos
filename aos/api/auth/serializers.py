"""Safe serializers for auth/session responses."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.account_status import get_account_state
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.accounts.serializers import seller_summary
from aos.services.localization_service import serialize_preference as serialize_localization_preference
from aos.services.user_preference_service import get_user_preference, is_country_locked


def serialize_auth_user(user: str) -> dict[str, Any]:
    row = frappe.db.get_value("User", user, ["email", "full_name", "first_name", "last_name", "user_image", "enabled"], as_dict=True) or {}
    profile = frappe.db.get_value("AOS Profile", user, ["display_name", "profile_image_media"], as_dict=True) or {}
    state = get_account_state(user)
    return {
        "id": public_account_id_for_user(user),
        "email": row.get("email") or user,
        "full_name": profile.get("display_name") or row.get("full_name") or row.get("first_name") or "",
        "first_name": row.get("first_name") or "",
        "last_name": row.get("last_name") or "",
        "user_image": row.get("user_image"),
        "enabled": bool(int(row.get("enabled") or 0)),
        "account_status": state.get("account_status"),
    }


def serialize_preference(user: str) -> dict[str, Any]:
    pref = get_user_preference(user)
    return serialize_localization_preference(pref, is_country_locked=is_country_locked(user)) if pref else {}


def serialize_roles(user: str) -> list[str]:
    try:
        roles = frappe.get_roles(user) or []
    except Exception:
        roles = []
    return sorted(role for role in roles if role not in {"All", "Guest"})


def serialize_seller_summary(user: str) -> dict[str, Any]:
    return seller_summary(user)


def serialize_session(*, sid: str | None = None, include_sid: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"authenticated": True, "expires_at": None}
    if include_sid:
        payload["sid"] = sid
    return payload


def serialize_auth_payload(user: str, *, sid: str | None = None, include_sid: bool = False) -> dict[str, Any]:
    return {
        "session": serialize_session(sid=sid, include_sid=include_sid),
        "user": serialize_auth_user(user),
        "preferences": serialize_preference(user),
        "roles": serialize_roles(user),
        "seller": serialize_seller_summary(user),
    }
