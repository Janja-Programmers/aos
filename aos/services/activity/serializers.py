"""Privacy-safe Activity Center serializers.

Activity rows deliberately snapshot user-facing history, but raw Frappe User
names, internal DocType names, moderation IDs, analytics session IDs, and
unbounded metadata must never cross the public API boundary.
"""

from __future__ import annotations

import json
from typing import Any

from frappe.utils import get_datetime_str

from aos.services.accounts.identity import (
    normalize_public_account_id,
    public_account_id_for_user,
)
from aos.services.sellers.identity import (
    normalize_public_seller_id,
    public_seller_id_for_name,
)

from .constants import PUBLIC_TARGET_KIND_BY_ROUTE

_METADATA_ALLOWLIST: dict[str, frozenset[str]] = {
    "ad_view": frozenset({"seller", "category", "location", "country", "ad_status", "price_type", "currency", "price"}),
    "ad_wishlist": frozenset({"seller", "category", "location", "country", "ad_status", "price_type", "currency", "price"}),
    "ad_posted": frozenset({"seller", "category", "location", "country", "ad_status", "price_type", "currency", "price"}),
    "ad_report": frozenset({"seller", "category", "location", "country", "ad_status", "price_type", "currency", "price", "reason"}),
    "short_watch": frozenset({"short_owner", "lifecycle_status", "moderation_status", "processing_status", "watch_ms"}),
    "short_like": frozenset({"short_owner", "lifecycle_status", "moderation_status", "processing_status"}),
    "short_comment": frozenset({"short_owner", "lifecycle_status", "moderation_status", "processing_status", "comment_id", "parent_comment_id", "is_reply", "comment_preview"}),
    "short_repost": frozenset({"short_owner", "lifecycle_status", "moderation_status", "processing_status"}),
    "short_report": frozenset({"short_owner", "lifecycle_status", "moderation_status", "processing_status", "reason"}),
    "user_search": frozenset({"query", "result_count"}),
    "user_follow": frozenset({"target_user", "target_display_name", "target_is_deleted", "target_is_live", "target_live_id"}),
    "user_block": frozenset({"target_user", "target_display_name", "target_is_deleted", "target_is_live", "target_live_id", "reason"}),
    "user_report": frozenset({"target_user", "target_display_name", "target_is_deleted", "target_is_live", "target_live_id", "reason"}),
    "live_host": frozenset({"live_id", "host_user", "live_status", "is_active", "started_at", "ended_at", "viewer_count"}),
    "live_join": frozenset({"live_id", "host_user", "live_status", "is_active", "started_at", "ended_at", "viewer_count"}),
    "live_comment": frozenset({"live_id", "host_user", "live_status", "is_active", "started_at", "ended_at", "viewer_count", "message_id", "parent_message_id", "is_reply", "comment_preview"}),
}


def _value(row: Any, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    return None


def _public_identity(key: str, value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    raw = value.strip()
    if key in {"short_owner", "host_user", "target_user"}:
        public_id = normalize_public_account_id(raw)
        if public_id:
            return public_id
        try:
            return public_account_id_for_user(raw)
        except Exception:
            return None
    if key == "seller":
        public_id = normalize_public_seller_id(raw)
        if public_id:
            return public_id
        try:
            return public_seller_id_for_name(raw)
        except Exception:
            return None
    return value


def _public_metadata(activity_type: str, raw: Any) -> dict[str, Any]:
    metadata = _metadata_dict(raw)
    allowed = _METADATA_ALLOWLIST.get(activity_type, frozenset())
    result: dict[str, Any] = {}
    for key in allowed:
        if key not in metadata:
            continue
        value = _public_identity(key, metadata.get(key))
        safe = _safe_scalar(value)
        if safe is not None:
            result[key] = safe
    return result


def serialize_activity(row: Any) -> dict[str, Any]:
    activity_type = str(_value(row, "activity_type") or "")
    route_type = str(_value(row, "route_type") or "")
    route_id = str(_value(row, "route_id") or "") or None
    target_kind = PUBLIC_TARGET_KIND_BY_ROUTE.get(route_type, "activity")

    # For public API consumers the navigation ID is the authoritative safe
    # target reference. This avoids exposing raw User emails or moderation row
    # names retained in target_name for legacy/internal history records.
    public_target_name = route_id

    occurred_at = _value(row, "occurred_at")
    last_occurrence_at = _value(row, "last_occurrence_at")
    return {
        "id": _value(row, "name"),
        "activity_group": _value(row, "activity_group"),
        "activity_type": activity_type,
        "status": _value(row, "status"),
        "target": {
            "doctype": target_kind,
            "name": public_target_name,
            "title": _value(row, "target_title") or None,
            "subtitle": _value(row, "target_subtitle") or None,
            "image": _value(row, "target_image") or None,
            "route_type": route_type or None,
            "route_id": route_id,
        },
        "metadata": _public_metadata(activity_type, _value(row, "metadata_json")),
        "occurred_at": get_datetime_str(occurred_at) if occurred_at else None,
        "last_occurrence_at": get_datetime_str(last_occurrence_at) if last_occurrence_at else None,
        "count": max(int(_value(row, "count", 0) or 0), 0),
    }
