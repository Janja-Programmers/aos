"""Privacy-safe serialization for Activity Center rows."""

from __future__ import annotations

import json
from typing import Any

from frappe.utils import get_datetime_str

from aos.services.accounts.identity import normalize_public_account_id
from aos.services.sellers.identity import normalize_public_seller_id

from .constants import ACTIVITY_COUNT_MAX, EVENT_SPECS, PUBLIC_TARGET_KIND_BY_ROUTE


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
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
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
    if key in {"host_user", "target_user"}:
        return normalize_public_account_id(raw) or None
    if key == "seller":
        return normalize_public_seller_id(raw) or None
    return value


def _public_metadata(activity_type: str, raw: Any) -> dict[str, Any]:
    metadata = _metadata_dict(raw)
    spec = EVENT_SPECS.get(activity_type) or {}
    allowed = frozenset(spec.get("metadata") or ())
    result: dict[str, Any] = {}
    for key in sorted(allowed):
        if key not in metadata:
            continue
        safe = _safe_scalar(_public_identity(key, metadata.get(key)))
        if safe is not None:
            result[key] = safe
    return result


def serialize_activity(row: Any) -> dict[str, Any]:
    activity_type = str(_value(row, "activity_type") or "")
    available = bool(_value(row, "resource_available", True))
    route_type = str(_value(row, "route_type") or "") if available else ""
    route_id = (str(_value(row, "route_id") or "") or None) if available else None
    target_kind = PUBLIC_TARGET_KIND_BY_ROUTE.get(route_type, "unavailable" if not available else "activity")
    occurred_at = _value(row, "occurred_at")
    last_occurrence_at = _value(row, "last_occurrence_at")
    return {
        "id": str(_value(row, "public_id") or ""),
        "activity_group": _value(row, "activity_group"),
        "activity_type": activity_type,
        "status": _value(row, "status"),
        "resource_available": available,
        "target": {
            "type": target_kind,
            "id": route_id,
            "title": (_value(row, "target_title") or None) if available else "Unavailable",
            "subtitle": (_value(row, "target_subtitle") or None) if available else None,
            "image": (_value(row, "target_image") or None) if available else None,
        },
        "metadata": _public_metadata(activity_type, _value(row, "metadata_json")) if available else {},
        "occurred_at": get_datetime_str(occurred_at) if occurred_at else None,
        "last_occurrence_at": get_datetime_str(last_occurrence_at) if last_occurrence_at else None,
        "count": min(max(int(_value(row, "count", 1) or 1), 1), ACTIVITY_COUNT_MAX),
    }
