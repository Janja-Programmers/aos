"""Bounded Reviews logs and metrics without user text or PII."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe

_ALLOWED_EVENTS = {
    "review.eligibility.checked",
    "review.created",
    "review.updated",
    "review.withdrawn",
    "review.published",
    "review.rejected",
    "review.hidden",
    "review.reported",
    "review.helpful.changed",
    "review.aggregate.updated",
    "review.aggregate.reconciled",
    "review.moderation.dispatched",
    "review.moderation.failed",
}
_ALLOWED_OUTCOMES = {"success", "rejected", "failure", "idempotent"}


def _opaque(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def review_log(
    event: str,
    *,
    review_id: Any = None,
    target_type: str = "ad",
    outcome: str = "success",
    operation: str | None = None,
    status: str | None = None,
    count: int | None = None,
) -> None:
    safe_event = event if event in _ALLOWED_EVENTS else "review.moderation.failed"
    safe_outcome = outcome if outcome in _ALLOWED_OUTCOMES else "failure"
    payload = {
        "event": safe_event,
        "review_ref": _opaque(review_id) if review_id else None,
        "target_type": str(target_type or "ad")[:24],
        "operation": str(operation or "")[:32] or None,
        "status": str(status or "")[:32] or None,
        "outcome": safe_outcome,
        "count": max(0, int(count or 0)),
    }
    try:
        frappe.logger("aos.reviews", allow_site=True).info(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
    except Exception:
        pass
