"""Accounts serializers with explicit public/private field allowlists."""

from __future__ import annotations

from typing import Any, Iterable

import frappe

from aos.api.shared.formatters import humanize_count, to_non_negative_int
from aos.services.localization import serialize_preference as serialize_localization_preference
from aos.services.localization.preferences import get_user_preference
from aos.services.media.media_service import MediaService
from aos.services.sellers.identity import public_seller_id_for_name
from aos.services.social.repository import SocialRepository

from .constants import ACCOUNT_STATUS_ACTIVE, ACCOUNT_STATUS_DELETED
from .identity import public_account_id_for_user
from .repository import AccountRepository


def _get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _avatar_url(row: Any) -> str | None:
    media_id = _get(row, "profile_image_media")
    if not media_id:
        return None
    try:
        return MediaService().get_public_url(media_id) or None
    except Exception:
        return None


def _display_name(row: Any, *, masked: bool = False) -> str:
    if masked:
        return "Deleted User"
    value = str(_get(row, "display_name") or "").strip()
    return value or "AOS User"


def _friends_count(user: str) -> int:
    return SocialRepository().friends_count(user=user)


def _counts(user: str, row: Any, *, hidden: bool) -> dict[str, Any]:
    followers = 0 if hidden else to_non_negative_int(_get(row, "total_followers"))
    following = 0 if hidden else to_non_negative_int(_get(row, "total_following"))
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
        ["status", "verification_type", "verified_on"],
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
    """Internal-only batch identity projection used by shared backend serializers."""

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


def serialize_public_profile_row(
    row: Any,
    *,
    relationship: dict[str, Any] | None = None,
    include_seller: bool = True,
) -> dict[str, Any]:
    user = str(_get(row, "user") or "")
    status = str(_get(row, "account_status", ACCOUNT_STATUS_ACTIVE) or ACCOUNT_STATUS_ACTIVE)
    deleted = status == ACCOUNT_STATUS_DELETED
    account_id = str(_get(row, "account_id") or "") or public_account_id_for_user(user)
    relation = dict(relationship or {})
    # Accounts has one canonical public identity field: top-level account_id.
    # Social may internally project target_user; do not leak that alias here.
    relation.pop("target_user", None)
    return {
        "account_id": account_id,
        "display_name": _display_name(row, masked=deleted),
        "bio": "" if deleted else str(_get(row, "bio") or ""),
        "avatar": None if deleted else _avatar_url(row),
        "is_deleted": deleted,
        "is_verified": bool(_get(row, "is_verified")) if not deleted else False,
        **_counts(user, row, hidden=deleted),
        "seller": (
            seller_summary(user, public=True)
            if include_seller and not deleted
            else {"is_seller": False, "seller_id": None, "status": None}
        ),
        **relation,
    }


def serialize_private_profile_row(row: Any) -> dict[str, Any]:
    """Owner-only profile projection; never expose Frappe User.name."""

    user = str(_get(row, "user") or "")
    public = serialize_public_profile_row(row, include_seller=False)
    preference = get_user_preference(user)
    public.update(
        {
            "email": _get(row, "email") or user,
            "legal_name": str(_get(row, "legal_name") or ""),
            "phone": str(_get(row, "phone") or ""),
            "date_of_birth": _get(row, "date_of_birth"),
            "gender": str(_get(row, "gender") or ""),
            "profile_image_media": _get(row, "profile_image_media") or None,
            "account_status": _get(row, "account_status", ACCOUNT_STATUS_ACTIVE),
            "enabled": bool(int(_get(row, "enabled", 0) or 0)),
            "purge_status": _get(row, "purge_status") or None,
            "purge_started_at": _get(row, "purge_started_at"),
            "purge_completed_at": _get(row, "purge_completed_at"),
            "preferences": serialize_localization_preference(preference) if preference else {},
            "roles": sorted(role for role in (frappe.get_roles(user) or []) if role not in {"All", "Guest"}),
            "seller": seller_summary(user),
            "verification": verification_summary(user),
            "can_edit": True,
        }
    )
    return public


def serialize_public_profile(user: str, *, relationship: dict[str, Any] | None = None) -> dict[str, Any]:
    row = AccountRepository.account_by_user(user) or frappe._dict({"user": user})
    return serialize_public_profile_row(row, relationship=relationship)


def serialize_private_profile(user: str) -> dict[str, Any]:
    row = AccountRepository.account_by_user(user) or frappe._dict({"user": user})
    return serialize_private_profile_row(row)
