"""Safe serializers for auth/session responses."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.auth.observability import log_auth_exception
from aos.services.accounts.serializers import seller_summary
from aos.services.localization import serialize_preference as serialize_localization_preference
from aos.services.media.media_service import MediaService
from aos.services.user_preference_service import get_user_preference, is_country_locked


def serialize_auth_user(user: str) -> dict[str, Any]:
    rows = frappe.db.sql(
        """
        SELECT u.email, u.enabled,
               p.name AS account_id, p.display_name, p.profile_image_media,
               p.account_status, p.is_verified
        FROM `tabUser` u
        INNER JOIN `tabAOS Profile` p ON p.user = u.name
        WHERE u.name = %s
        LIMIT 1
        """,
        (user,),
        as_dict=True,
    )
    if not rows or not rows[0].account_id:
        raise RuntimeError("Authentication account identity invariant is missing")
    row = rows[0]
    avatar = None
    if row.profile_image_media:
        try:
            avatar = MediaService().get_public_url(row.profile_image_media) or None
        except Exception as exc:  # Optional presentation data must not take Authentication down.
            log_auth_exception(
                "AOS Auth Avatar Resolution Failed",
                exc,
                operation="serialize_auth_user_avatar",
            )
    return {
        "account_id": row.account_id,
        "email": row.email or user,
        "display_name": row.display_name or "AOS User",
        "avatar": avatar,
        "enabled": bool(int(row.enabled or 0)),
        "account_status": row.account_status,
        "is_verified": bool(row.is_verified),
    }


def serialize_preference(
    user: str,
    *,
    country_locked: bool | None = None,
    preference: Any | None = None,
) -> dict[str, Any]:
    pref = preference or get_user_preference(user)
    if not pref:
        return {}
    locked = is_country_locked(user) if country_locked is None else bool(country_locked)
    return serialize_localization_preference(pref, is_country_locked=locked)


def serialize_roles(user: str) -> list[str]:
    roles = frappe.get_roles(user) or []
    return sorted(role for role in roles if role not in {"All", "Guest"})


def serialize_seller_summary(user: str) -> dict[str, Any]:
    return seller_summary(user)


def serialize_session(*, sid: str | None = None, include_sid: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"authenticated": True}
    if include_sid:
        payload["sid"] = sid
    return payload


def serialize_auth_bootstrap(user: str, *, preference: Any | None = None) -> dict[str, Any]:
    seller = serialize_seller_summary(user)
    return {
        "user": serialize_auth_user(user),
        "preferences": serialize_preference(
            user,
            country_locked=bool(seller.get("seller_id")),
            preference=preference,
        ),
        "roles": serialize_roles(user),
        "seller": seller,
    }


def serialize_auth_payload(
    user: str,
    *,
    sid: str | None = None,
    include_sid: bool = False,
    preference: Any | None = None,
) -> dict[str, Any]:
    return {
        "session": serialize_session(sid=sid, include_sid=include_sid),
        **serialize_auth_bootstrap(user, preference=preference),
    }
