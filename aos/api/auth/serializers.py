"""Safe serializers for auth/session responses."""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.accounts.identity import normalize_public_account_id
from aos.services.accounts.serializers import seller_summary
from aos.services.localization import serialize_preference as serialize_localization_preference
from aos.services.user_preference_service import get_user_preference, is_country_locked


def serialize_auth_user(user: str) -> dict[str, Any]:
    rows = frappe.db.sql(
        """
        SELECT u.email, u.full_name, u.first_name, u.last_name, u.user_image, u.enabled,
               p.public_id, p.display_name, p.account_status
        FROM `tabUser` u
        INNER JOIN `tabAOS Profile` p ON p.user = u.name
        WHERE u.name = %s
        LIMIT 1
        """,
        (user,),
        as_dict=True,
    )
    row = rows[0] if rows else {}
    public_id = normalize_public_account_id(row.get("public_id") if row else "")
    if not public_id:
        raise RuntimeError("Authentication account public identity invariant is missing")
    return {
        "id": public_id,
        "email": row.get("email") or user,
        "full_name": row.get("display_name") or row.get("full_name") or row.get("first_name") or "",
        "first_name": row.get("first_name") or "",
        "last_name": row.get("last_name") or "",
        "user_image": row.get("user_image"),
        "enabled": bool(int(row.get("enabled") or 0)),
        "account_status": row.get("account_status"),
    }


def serialize_preference(user: str, *, country_locked: bool | None = None) -> dict[str, Any]:
    pref = get_user_preference(user)
    if not pref:
        return {}
    locked = is_country_locked(user) if country_locked is None else bool(country_locked)
    return serialize_localization_preference(pref, is_country_locked=locked)


def serialize_roles(user: str) -> list[str]:
    # A dependency/query failure must not silently turn a privileged user's role
    # set into an empty list in the public bootstrap payload.
    roles = frappe.get_roles(user) or []
    return sorted(role for role in roles if role not in {"All", "Guest"})


def serialize_seller_summary(user: str) -> dict[str, Any]:
    return seller_summary(user)


def serialize_session(*, sid: str | None = None, include_sid: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"authenticated": True, "expires_at": None}
    if include_sid:
        payload["sid"] = sid
    return payload


def serialize_auth_bootstrap(user: str) -> dict[str, Any]:
    """Serialize all DB-backed bootstrap state before creating a new session.

    Frappe session creation may commit internally. Doing these reads first avoids
    creating a valid session and then reporting login failure because an
    unrelated bootstrap serialization query failed afterwards.
    """
    seller = serialize_seller_summary(user)
    return {
        "user": serialize_auth_user(user),
        "preferences": serialize_preference(user, country_locked=bool(seller.get("seller_id"))),
        "roles": serialize_roles(user),
        "seller": seller,
    }


def serialize_auth_payload(user: str, *, sid: str | None = None, include_sid: bool = False) -> dict[str, Any]:
    return {
        "session": serialize_session(sid=sid, include_sid=include_sid),
        **serialize_auth_bootstrap(user),
    }
