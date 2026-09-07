"""Privacy-safe Seller serializers."""

from __future__ import annotations

import re
from datetime import time, timedelta
from typing import Any

import frappe
from frappe.utils import formatdate

from aos.api.shared.formatters import format_rating, humanize_count, to_float, to_non_negative_int
from aos.api.shared.live_state import get_users_live_state
from aos.api.shared.user_display import get_user_display
from aos.services.media.media_service import MediaService
from aos.services.seller_response_metrics import format_response_rate, format_response_time

from .constants import STATUS_ACTIVE
from .identity import migration_fallback_public_seller_id, normalize_public_seller_id
from .policy import seller_capabilities

_EMAIL_LIKE_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _clamp_percentage(value: Any) -> float:
    return max(0.0, min(to_float(value), 100.0))


def _banner_url(row: Any) -> str | None:
    media_id = str(_get(row, "shop_banner_media") or "").strip()
    if media_id:
        try:
            return MediaService().get_public_url(media_id) or None
        except Exception:
            return None
    legacy = str(_get(row, "shop_banner") or "").strip()
    return legacy if legacy.startswith("https://") else None


def _time_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, timedelta):
        seconds = max(0, int(value.total_seconds())) % (24 * 60 * 60)
        hour, remainder = divmod(seconds, 3600)
        minute, second = divmod(remainder, 60)
        return f"{hour:02d}:{minute:02d}:{second:02d}"
    text = str(value).strip()
    match = re.fullmatch(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?", text)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second") or 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def serialize_operating_hours(rows: list[Any] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows or []:
        is_open = bool(_get(row, "is_open"))
        result.append(
            {
                "day_of_week": _get(row, "day_of_week"),
                "is_open": is_open,
                "open_time": _time_string(_get(row, "open_time")) if is_open else None,
                "close_time": _time_string(_get(row, "close_time")) if is_open else None,
            }
        )
    return result


def serialize_location(row: Any, *, exact: bool) -> dict[str, Any]:
    has_location = bool(_get(row, "has_location"))
    country_code = str(_get(row, "country_code") or "").strip().upper()
    if not has_location or len(country_code) != 2:
        if exact:
            return {
                "has_location": False,
                "name": None,
                "latitude": None,
                "longitude": None,
                "display_address": None,
                "locality": None,
                "region": None,
                "country_code": None,
                "instructions": None,
                "updated_at": None,
            }
        return {"has_location": False}
    payload: dict[str, Any] = {
        "has_location": True,
        "name": _get(row, "location_name") or None,
        "locality": _get(row, "locality") or None,
        "region": _get(row, "region") or None,
        "country_code": country_code,
    }
    distance = _get(row, "distance_km")
    if distance is not None:
        try:
            distance_value = max(0.0, round(float(distance), 2))
            payload["distance_km"] = distance_value
            payload["distance_display"] = (
                f"{round(distance_value * 1000):.0f} m" if distance_value < 1 else f"{distance_value:g} km"
            )
        except (TypeError, ValueError, OverflowError):
            pass
    if exact:
        try:
            latitude = float(_get(row, "latitude"))
            longitude = float(_get(row, "longitude"))
            if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
                raise ValueError("coordinate outside supported bounds")
        except (TypeError, ValueError, OverflowError):
            return {
                "has_location": False,
                "name": None,
                "latitude": None,
                "longitude": None,
                "display_address": None,
                "locality": None,
                "region": None,
                "country_code": None,
                "instructions": None,
                "updated_at": None,
            }
        payload.update(
            {
                "latitude": latitude,
                "longitude": longitude,
                "display_address": _get(row, "display_address") or None,
                "instructions": _get(row, "location_instructions") or None,
                "updated_at": _get(row, "location_updated_at") or None,
            }
        )
    return payload


def _metrics(row: Any) -> dict[str, Any]:
    rating = max(0.0, min(to_float(_get(row, "rating")), 5.0))
    reviews = to_non_negative_int(_get(row, "total_reviews"))
    ads = to_non_negative_int(_get(row, "total_ads"))
    followers = to_non_negative_int(_get(row, "total_followers"))
    following = to_non_negative_int(_get(row, "total_following"))
    friends = to_non_negative_int(_get(row, "total_friends"))
    response_seconds = to_non_negative_int(_get(row, "chat_response_time_seconds"))
    response_rate = _clamp_percentage(_get(row, "chat_response_rate"))
    samples = to_non_negative_int(_get(row, "chat_response_sample_size"))
    requests = to_non_negative_int(_get(row, "chat_response_requests"))
    return {
        "rating": rating,
        "rating_display": format_rating(rating),
        "total_reviews": reviews,
        "total_reviews_display": humanize_count(reviews),
        "total_ads": ads,
        "total_ads_display": humanize_count(ads),
        "total_followers": followers,
        "total_followers_display": humanize_count(followers),
        "total_following": following,
        "total_following_display": humanize_count(following),
        "total_friends": friends,
        "total_friends_display": humanize_count(friends),
        "response_time_seconds": response_seconds if samples > 0 else None,
        "response_time_display": format_response_time(response_seconds, sample_size=samples),
        "response_rate": round(response_rate, 2),
        "response_rate_display": format_response_rate(response_rate, response_requests=requests),
        "response_sample_size": samples,
        "response_requests": requests,
        "response_metrics_updated_at": _get(row, "response_metrics_updated_at") or None,
    }


def guest_relationship(*, target_user: str | None) -> dict[str, Any]:
    return {
        "target_user": target_user,
        "is_self": False,
        "is_following": False,
        "is_followed_by": False,
        "is_friend": False,
        "relationship_status": "none",
        "action_label": "Follow",
        "is_blocked_by_me": False,
        "has_blocked_me": False,
        "is_blocked": False,
        "block_status": "none",
    }


def serialize_public_list_item(
    row: Any,
    *,
    display: dict[str, Any],
    relationship: dict[str, Any],
) -> dict[str, Any]:
    identity = display or {}
    seller_id = normalize_public_seller_id(_get(row, "public_id")) or migration_fallback_public_seller_id(
        _get(row, "name")
    )
    return {
        "seller": seller_id,
        "seller_id": seller_id,
        "account_id": identity.get("account_id"),
        "display_name": identity.get("display_name") or "AOS User",
        "avatar": identity.get("avatar"),
        "is_deleted": bool(identity.get("is_deleted")),
        "is_live": bool(identity.get("is_live")),
        "live_id": identity.get("live_id"),
        "live_status": identity.get("live_status"),
        "live_title": identity.get("live_title"),
        "live_cover_image": identity.get("live_cover_image"),
        "live_started_at": identity.get("live_started_at"),
        "live_viewer_count": int(identity.get("live_viewer_count") or 0),
        "business_category": _get(row, "business_category") or None,
        "seller_type": _get(row, "seller_type"),
        "is_verified": bool(_get(row, "is_verified")),
        "shop_banner": _banner_url(row),
        "location": serialize_location(row, exact=False),
        **_metrics(row),
        **relationship,
    }


def serialize_public_detail(
    row: Any,
    *,
    profile: Any,
    operating_hours: list[Any],
    relationship: dict[str, Any],
) -> dict[str, Any]:
    display = get_user_display(str(_get(row, "user") or ""))
    payload = serialize_public_list_item(
        {
            **(dict(row) if isinstance(row, dict) else row.as_dict()),
            "total_followers": _get(profile, "total_followers"),
            "total_following": _get(profile, "total_following"),
            "is_verified": _get(profile, "is_verified"),
        },
        display=display,
        relationship=relationship,
    )
    payload.update(
        {
            "status": STATUS_ACTIVE,
            "about_business": _get(row, "about_business") or None,
            "shop_banner_media": _get(row, "shop_banner_media") or None,
            "shop_banner_media_id": _get(row, "shop_banner_media") or None,
            "location": serialize_location(row, exact=True),
            "joined": formatdate(_get(row, "creation"), "MMM yyyy") if _get(row, "creation") else None,
            "can_edit": bool(relationship.get("is_self")),
            "operating_hours": serialize_operating_hours(operating_hours),
        }
    )
    return payload


def serialize_status(row: Any | None, *, verified: bool = False) -> dict[str, Any]:
    if not row:
        return {
            "is_seller": False,
            "seller_id": None,
            "status": None,
            "seller_type": None,
            "business_category": None,
            "storefront_version": None,
            "is_verified": bool(verified),
            **seller_capabilities(None),
        }
    return {
        "is_seller": True,
        "seller_id": normalize_public_seller_id(_get(row, "public_id"))
        or migration_fallback_public_seller_id(_get(row, "name")),
        "status": _get(row, "status"),
        "seller_type": _get(row, "seller_type"),
        "business_category": _get(row, "business_category") or None,
        "storefront_version": to_non_negative_int(_get(row, "storefront_version")),
        "has_location": bool(_get(row, "has_location")),
        "is_verified": bool(verified),
        **seller_capabilities(row),
    }


def display_map(users: list[str]) -> dict[str, dict[str, Any]]:
    """Bulk-load privacy-safe Account displays for Seller discovery.

    The shared single-user serializer remains appropriate for Seller detail.
    Public discovery is bounded to 50 records and must not issue profile, User,
    public-ID, avatar-Media, and live-state queries once per Seller.
    """

    unique = sorted({str(user or "").strip() for user in users if user})
    if not unique:
        return {}
    rows = frappe.db.sql(
        """
        SELECT u.name AS internal_user, u.full_name, u.first_name, u.user_image,
               u.enabled, p.name AS account_id, p.display_name, p.profile_image_media,
               p.account_status
        FROM `tabUser` u
        INNER JOIN `tabAOS Profile` p ON p.user = u.name
        WHERE u.name IN %(users)s
        """,
        {"users": tuple(unique)},
        as_dict=True,
    )
    media_ids = [str(row.profile_image_media) for row in rows if row.profile_image_media]
    avatar_urls = MediaService().get_public_url_map(media_ids)
    live_by_user = get_users_live_state(unique)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        user = str(row.internal_user or "")
        status = str(row.account_status or "Active")
        deleted = status == "Deleted"
        unavailable = deleted or not bool(int(row.enabled or 0)) or status != "Active"
        hidden = unavailable
        account_id = str(row.account_id or "").strip() or None
        raw_name = str(row.display_name or row.full_name or row.first_name or "").strip()
        display_name = raw_name if raw_name and not _EMAIL_LIKE_RE.fullmatch(raw_name) else "AOS User"
        if deleted:
            display_name = "Deleted User"
        elif unavailable:
            display_name = "Unavailable User"
        avatar = None
        if not hidden:
            avatar = avatar_urls.get(str(row.profile_image_media or "")) or row.user_image or None
        live = {
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
        if not hidden and live_by_user.get(user):
            live.update(live_by_user[user])
            live["is_live"] = bool(live.get("is_live"))
            live["live_viewer_count"] = max(0, int(live.get("live_viewer_count") or 0))
        result[user] = {
            "account_id": account_id,
            "display_name": display_name,
            "avatar": avatar,
            "is_deleted": deleted,
            **live,
        }
    return result
