"""Bounded Maps logs without coordinates, queries, addresses, or user IDs."""

from __future__ import annotations

import json
from typing import Any

import frappe

_ALLOWED_EVENTS = {
    "maps.autocomplete",
    "maps.search",
    "maps.reverse",
    "maps.route",
    "maps.seller_location.read",
    "maps.seller_location.updated",
    "maps.seller_location.removed",
    "maps.seller_points.listed",
    "maps.provider.fallback",
}
_ALLOWED_OUTCOMES = {"success", "rejected", "failure", "cache_hit", "idempotent"}


def maps_log(
    event: str,
    *,
    operation: str | None = None,
    provider: str | None = None,
    outcome: str = "success",
    count: int | None = None,
    cached: bool | None = None,
    status: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "event": event if event in _ALLOWED_EVENTS else "maps.provider.fallback",
        "operation": str(operation or "")[:40] or None,
        "provider": str(provider or "")[:20] or None,
        "outcome": outcome if outcome in _ALLOWED_OUTCOMES else "failure",
        "count": max(0, int(count or 0)),
        "cached": bool(cached) if cached is not None else None,
        "status": str(status or "")[:32] or None,
    }
    try:
        frappe.logger("aos.maps", allow_site=True).info(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
    except Exception:
        pass
