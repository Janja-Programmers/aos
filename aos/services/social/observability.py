"""Privacy-safe structured Social observability."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Iterator

import frappe

_ALLOWED_OPERATIONS = {
    "follow", "unfollow", "relationship", "following_list",
    "followers_list", "friends_list", "search", "block", "unblock",
    "block_status", "blocked_list", "unknown",
}
_ALLOWED_OUTCOMES = {"success", "idempotent", "rejected", "conflict", "failure"}
_ALLOWED_REASONS = {
    "none", "validation", "unknown_field", "self_action",
    "blocked", "unavailable", "duplicate", "cursor", "rate_limit", "internal",
}


def social_log(operation: str, *, outcome: str, latency_ms: int = 0, reason: str = "none", changed: bool | None = None, notification: str | None = None, count: int | None = None) -> None:
    payload = {
        "operation": operation if operation in _ALLOWED_OPERATIONS else "unknown",
        "outcome": outcome if outcome in _ALLOWED_OUTCOMES else "failure",
        "latency_ms": max(0, min(int(latency_ms or 0), 3_600_000)),
        "reason": reason if reason in _ALLOWED_REASONS else "internal",
        "changed": None if changed is None else bool(changed),
        "notification": str(notification or "")[:24] or None,
        "count": None if count is None else max(0, min(int(count), 1_000_000)),
    }
    try:
        frappe.logger("aos.social", allow_site=True).info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except Exception:
        pass


@contextmanager
def observe(operation: str) -> Iterator[dict[str, Any]]:
    started = time.monotonic()
    state: dict[str, Any] = {"outcome": "success", "reason": "none"}
    try:
        yield state
    except Exception:
        if state.get("outcome") == "success":
            state["outcome"] = "failure"
        if state.get("reason") == "none":
            state["reason"] = "internal"
        raise
    finally:
        social_log(
            operation,
            outcome=str(state.get("outcome") or "success"),
            reason=str(state.get("reason") or "none"),
            latency_ms=int((time.monotonic() - started) * 1000),
            changed=state.get("changed"),
            notification=state.get("notification"),
            count=state.get("count"),
        )
