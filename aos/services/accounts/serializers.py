"""Context-specific Accounts serializers built from privacy-safe primitives."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.formatters import humanize_count, to_non_negative_int
from aos.services.localization_service import serialize_preference as serialize_localization_preference
from aos.services.media.media_service import MediaService
from aos.services.user_preference_service import get_user_preference, is_country_locked

from .constants import ACCOUNT_STATUS_ACTIVE
from .identity import public_account_id_for_user


def _get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _profile(user: str):
    fields = [
        "name", "user", "public_id", "display_name", "legal_name", "bio", "phone",
        "date_of_birth", "gender", "location", "profile_image_media", "account_status",
        "is_deleted", "deactivated_at", "deleted_at", "restore_deadline", "total_followers",
        "total_following", "is_verified",
    ]
    try:
        meta = frappe.get_meta("AOS Profile")
        fields = [field for field in fields if meta.has_field(field) or field in {"name", "user"}]
    except Exception:
        pass
    return frappe.db.get_value("AOS Profile", user, fields, as_dict=True) or {}


def _user(user: str):
    return frappe.db.get_value(
        "User",
        user,
        ["name", "email", "full_name", "first_name", "last_name", "user_image", "enabled"],
        as_dict=True,
    ) or {}


def _avatar_url(profile: Any, user_row: Any) -> str | None:
    media_id = _get(profile, "profile_image_media")
    if media_id:
        try:
            return MediaService().get_public_url(media_id) or None
        except Exception:
            pass
    return _get(user_row, "user_image") or None


def _display_name(profile: Any, user_row: Any, *, masked: bool = False) -> str:
    if masked:
        return "Deleted User"
    value = str(
        _get(profile, "display_name")
        or _get(user_row, "full_name")
        or _get(user_row, "first_name")
        or ""
    ).strip()
    # User.name is commonly an email. Never use it as a public display fallback.
    return value or "AOS User"


def _friends_count(user: str) -> int:
    rows = frappe.db.sql(
        """
        SELECT COUNT(*) AS count
        FROM `tabAOS Follow` a
        INNER JOIN `tabAOS Follow` b
          ON b.follower_user = a.following_user
         AND b.following_user = a.follower_user
        WHERE a.follower_user = %s
        """,
        (user,),
        as_dict=True,
    )
    return to_non_negative_int(rows[0].get("count") if rows else 0)


def _counts(user: str, profile: Any, *, hidden: bool) -> dict[str, Any]:
    followers = 0 if hidden else to_non_negative_int(_get(profile, "total_followers"))
    following = 0 if hidden else to_non_negative_int(_get(profile, "total_following"))
    friends = 0 if hidden else _friends_count(user)
    payload = {
        "followers_count": followers,
        "following_count": following,
        "friends_count": friends,
    }
    payload.update({f"{key}_display": humanize_count(value) for key, value in payload.items()})
    # Legacy additive compatibility.
    payload.update(
        {
            "total_followers": followers,
            "total_followers_display": humanize_count(followers),
            "total_following": following,
            "total_following_display": humanize_count(following),
            "total_friends": friends,
            "total_friends_display": humanize_count(friends),
        }
    )
    return payload


def seller_summary(user: str, *, public: bool = False) -> dict[str, Any]:
    fields = ["name", "status", "seller_type", "business_category", "rating", "total_reviews"]
    seller = frappe.db.get_value("AOS Seller", {"user": user}, fields, as_dict=True)
    if not seller:
        return {"is_seller": False, "seller_id": None, "status": None}
    payload = {
        "is_seller": seller.status == "Active",
        "seller_id": seller.name,
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
    return {
        "status": row.status,
        "verification_type": row.verification_type,
        "verified_on": row.verified_on,
    }


def serialize_internal_identity(user: str) -> dict[str, Any]:
    profile = _profile(user)
    user_row = _user(user)
    status = _get(profile, "account_status", ACCOUNT_STATUS_ACTIVE) or ACCOUNT_STATUS_ACTIVE
    hidden = bool(int(_get(profile, "is_deleted", 0) or 0)) or status == "Deleted"
    return {
        "internal_user": user,
        "account_id": public_account_id_for_user(user),
        "display_name": _display_name(profile, user_row, masked=hidden),
        "avatar": None if hidden else _avatar_url(profile, user_row),
        "account_status": status,
        "is_deleted": hidden,
        "is_deactivated": status == "Deactivated",
    }


def serialize_public_profile(user: str, *, relationship: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = _profile(user)
    user_row = _user(user)
    status = _get(profile, "account_status", ACCOUNT_STATUS_ACTIVE) or ACCOUNT_STATUS_ACTIVE
    deleted = bool(int(_get(profile, "is_deleted", 0) or 0)) or status == "Deleted"
    deactivated = status == "Deactivated"
    hidden = deleted or deactivated
    account_id = public_account_id_for_user(user)
    relation = dict(relationship or {})
    if relation:
        relation["target_user"] = account_id
    return {
        "account_id": account_id,
        "user": account_id,
        "display_name": _display_name(profile, user_row, masked=hidden),
        "full_name": _display_name(profile, user_row, masked=hidden),
        "bio": "" if hidden else str(_get(profile, "bio") or ""),
        "avatar": None if hidden else _avatar_url(profile, user_row),
        "user_image": None if hidden else _avatar_url(profile, user_row),
        "is_deleted": deleted,
        "is_deactivated": deactivated,
        "is_verified": bool(_get(profile, "is_verified")) if not hidden else False,
        **_counts(user, profile, hidden=hidden),
        "seller": seller_summary(user, public=True) if not hidden else {"is_seller": False, "seller_id": None, "status": None},
        **relation,
    }


def serialize_private_profile(user: str) -> dict[str, Any]:
    profile = _profile(user)
    user_row = _user(user)
    public = serialize_public_profile(user)
    preference = get_user_preference(user)
    preferences = (
        serialize_localization_preference(preference, is_country_locked=is_country_locked(user))
        if preference
        else {}
    )
    public.update(
        {
            "internal_user": user,
            "email": _get(user_row, "email") or user,
            "legal_name": str(_get(profile, "legal_name") or ""),
            "phone": str(_get(profile, "phone") or ""),
            "date_of_birth": _get(profile, "date_of_birth"),
            "gender": str(_get(profile, "gender") or ""),
            "location": str(_get(profile, "location") or ""),
            "profile_image_media": _get(profile, "profile_image_media") or None,
            "account_status": _get(profile, "account_status", ACCOUNT_STATUS_ACTIVE),
            "enabled": bool(int(_get(user_row, "enabled", 0) or 0)),
            "preferences": preferences,
            "roles": sorted(role for role in (frappe.get_roles(user) or []) if role not in {"All", "Guest"}),
            "seller": seller_summary(user),
            "verification": verification_summary(user),
            "can_edit": True,
        }
    )
    return public
