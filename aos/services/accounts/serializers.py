"""Accounts serializers with explicit public/private field allowlists."""

from __future__ import annotations

from typing import Any, Iterable

import frappe

from aos.services.localization import serialize_preference as serialize_localization_preference
from aos.services.media.media_service import MediaService
from aos.services.sellers.identity import public_seller_id_for_name
from aos.services.social.repository import SocialRepository
from aos.services.user_preference_service import get_user_preference, is_country_locked
from aos.api.shared.formatters import humanize_count, to_non_negative_int

from .constants import ACCOUNT_STATUS_ACTIVE, ACCOUNT_STATUS_DELETED
from .identity import public_account_id_for_user


def _get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _profile(user: str):
    return frappe.db.get_value(
        "AOS Profile",
        {"user": user},
        [
            "name", "user", "display_name", "legal_name", "bio", "phone",
            "date_of_birth", "gender", "profile_image_media", "account_status",
            "deleted_at", "restore_deadline", "purge_status", "purge_started_at",
            "purge_completed_at", "total_followers", "total_following", "is_verified",
        ],
        as_dict=True,
    ) or {}


def _user(user: str):
    return frappe.db.get_value(
        "User", user, ["name", "email", "enabled"], as_dict=True
    ) or {}


def _avatar_url(profile: Any) -> str | None:
    media_id = _get(profile, "profile_image_media")
    if media_id:
        try:
            return MediaService().get_public_url(media_id) or None
        except Exception:
            pass
    return None


def _display_name(profile: Any, *, masked: bool = False) -> str:
    if masked:
        return "Deleted User"
    value = str(_get(profile, "display_name") or "").strip()
    return value or "AOS User"


def _friends_count(user: str) -> int:
    return SocialRepository().friends_count(user=user)


def _counts(user: str, profile: Any, *, hidden: bool) -> dict[str, Any]:
    followers = 0 if hidden else to_non_negative_int(_get(profile, "total_followers"))
    following = 0 if hidden else to_non_negative_int(_get(profile, "total_following"))
    friends = 0 if hidden else _friends_count(user)
    return {
        "followers_count": followers,
        "followers_count_display": humanize_count(followers),
        "following_count": following,
        "following_count_display": humanize_count(following),
        "friends_count": friends,
        "friends_count_display": humanize_count(friends),
    }


def seller_summary(user: str, *, public: bool = False) -> dict[str, Any]:
    fields = ["name", "status", "seller_type", "business_category", "rating", "total_reviews"]
    seller = frappe.db.get_value("AOS Seller", {"user": user}, fields, as_dict=True)
    if not seller:
        return {"is_seller": False, "seller_id": None, "status": None}
    payload = {
        "is_seller": seller.status == "Active",
        "seller_id": public_seller_id_for_name(seller.name),
        "status": seller.status,
        "seller_type": seller.seller_type,
        "business_category": seller.business_category,
        "rating": float(seller.rating or 0),
        "total_reviews": to_non_negative_int(seller.total_reviews),
    }
    if public and seller.status not in {"Active", "Suspended"}:
        return {"is_seller": False, "seller_id": None, "status": None}
    return payload


def verification_summary(user: str) -> dict[str, Any]:
    row = frappe.db.get_value(
        "AOS Verification Request",
        {"user": user},
        ["name", "status", "verification_type", "verified_on"],
        order_by="creation desc",
        as_dict=True,
    )
    if not row:
        return {"status": None, "verification_type": None, "verified_on": None}
    return {"status": row.status, "verification_type": row.verification_type, "verified_on": row.verified_on}


def serialize_internal_identity(user: str) -> dict[str, Any]:
    return serialize_internal_identity_map([user]).get(user) or {
        "internal_user": user,
        "account_id": public_account_id_for_user(user),
        "display_name": "AOS User",
        "avatar": None,
        "account_status": ACCOUNT_STATUS_ACTIVE,
        "is_deleted": False,
        "enabled": False,
    }


def serialize_internal_identity_map(users: Iterable[str]) -> dict[str, dict[str, Any]]:
    unique = sorted({str(user).strip() for user in users if user})
    if not unique:
        return {}
    rows = frappe.db.sql(
        """
        SELECT u.name AS user, COALESCE(u.enabled, 0) AS enabled,
               p.name AS account_id, p.display_name, p.profile_image_media,
               COALESCE(NULLIF(p.account_status, ''), %(active)s) AS account_status
        FROM `tabUser` u
        LEFT JOIN `tabAOS Profile` p ON p.user = u.name
        WHERE u.name IN %(users)s
        """,
        {"users": tuple(unique), "active": ACCOUNT_STATUS_ACTIVE},
        as_dict=True,
    )
    by_user = {str(row.user): row for row in rows}
    media_ids = [str(row.profile_image_media) for row in rows if row.profile_image_media]
    try:
        media_urls = MediaService().get_public_url_map(media_ids)
    except Exception:
        media_urls = {}

    result: dict[str, dict[str, Any]] = {}
    for user in unique:
        row = by_user.get(user)
        if not row:
            result[user] = {
                "internal_user": user,
                "account_id": public_account_id_for_user(user),
                "display_name": "AOS User",
                "avatar": None,
                "account_status": ACCOUNT_STATUS_ACTIVE,
                "is_deleted": False,
                "enabled": False,
            }
            continue
        status = str(row.account_status or ACCOUNT_STATUS_ACTIVE)
        deleted = status == ACCOUNT_STATUS_DELETED
        display_name = "Deleted User" if deleted else str(row.display_name or "AOS User").strip()
        media_id = str(row.profile_image_media or "")
        result[user] = {
            "internal_user": user,
            "account_id": str(row.account_id or "") or public_account_id_for_user(user),
            "display_name": display_name or "AOS User",
            "avatar": None if deleted else (media_urls.get(media_id) or None),
            "account_status": status,
            "is_deleted": deleted,
            "enabled": bool(int(row.enabled or 0)),
        }
    return result


def serialize_public_profile(user: str, *, relationship: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = _profile(user)
    user_row = _user(user)
    status = _get(profile, "account_status", ACCOUNT_STATUS_ACTIVE) or ACCOUNT_STATUS_ACTIVE
    deleted = status == ACCOUNT_STATUS_DELETED
    account_id = public_account_id_for_user(user)
    relation = dict(relationship or {})
    if relation:
        relation["target_user"] = account_id
    return {
        "account_id": account_id,
        "display_name": _display_name(profile, masked=deleted),
        "bio": "" if deleted else str(_get(profile, "bio") or ""),
        "avatar": None if deleted else _avatar_url(profile),
        "is_deleted": deleted,
        "is_verified": bool(_get(profile, "is_verified")) if not deleted else False,
        **_counts(user, profile, hidden=deleted),
        "seller": seller_summary(user, public=True) if not deleted else {"is_seller": False, "seller_id": None, "status": None},
        **relation,
    }


def serialize_private_profile(user: str) -> dict[str, Any]:
    profile = _profile(user)
    user_row = _user(user)
    public = serialize_public_profile(user)
    preference = get_user_preference(user)
    preferences = (
        serialize_localization_preference(preference, is_country_locked=is_country_locked(user)) if preference else {}
    )
    public.update(
        {
            "internal_user": user,
            "email": _get(user_row, "email") or user,
            "legal_name": str(_get(profile, "legal_name") or ""),
            "phone": str(_get(profile, "phone") or ""),
            "date_of_birth": _get(profile, "date_of_birth"),
            "gender": str(_get(profile, "gender") or ""),
            "profile_image_media": _get(profile, "profile_image_media") or None,
            "account_status": _get(profile, "account_status", ACCOUNT_STATUS_ACTIVE),
            "enabled": bool(int(_get(user_row, "enabled", 0) or 0)),
            "purge_status": _get(profile, "purge_status") or None,
            "purge_started_at": _get(profile, "purge_started_at"),
            "purge_completed_at": _get(profile, "purge_completed_at"),
            "preferences": preferences,
            "roles": sorted(role for role in (frappe.get_roles(user) or []) if role not in {"All", "Guest"}),
            "seller": seller_summary(user),
            "verification": verification_summary(user),
            "can_edit": True,
        }
    )
    return public
