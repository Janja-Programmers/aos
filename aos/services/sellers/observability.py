"""Bounded Seller logs without user identifiers or storefront text."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe

_ALLOWED_EVENTS = {
    "seller.listed",
    "seller.fetched",
    "seller.status.checked",
    "seller.storefront.updated",
    "seller.status.changed",
    "seller.aggregate.reconciled",
}
_ALLOWED_OUTCOMES = {"success", "rejected", "failure", "idempotent"}


def _opaque(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def seller_log(
    event: str,
    *,
    seller_id: Any = None,
    operation: str | None = None,
    status: str | None = None,
    outcome: str = "success",
    count: int | None = None,
) -> None:
    payload = {
        "event": event if event in _ALLOWED_EVENTS else "seller.status.checked",
        "seller_ref": _opaque(seller_id) if seller_id else None,
        "operation": str(operation or "")[:32] or None,
        "status": str(status or "")[:24] or None,
        "outcome": outcome if outcome in _ALLOWED_OUTCOMES else "failure",
        "count": max(0, int(count or 0)),
    }
    try:
        frappe.logger("aos.sellers", allow_site=True).info(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
    except Exception:
        pass
