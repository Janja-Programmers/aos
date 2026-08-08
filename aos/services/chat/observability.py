"""Privacy-safe Chat operational logging."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

import frappe


_ALLOWED_OUTCOMES = {"success", "rejected", "conflict", "failure"}
_ALLOWED_REASONS = {
    "none", "validation", "unknown_field", "alias_conflict", "identifier", "access",
    "state", "rate_limit", "dependency", "duplicate", "pagination", "not_found",
}


def chat_log(
    operation: str,
    *,
    outcome: str,
    reason: str = "none",
    latency_ms: int = 0,
    count: int | None = None,
) -> None:
    """Log only bounded categories; never Chat IDs, users, text, cursors or IPs."""
    op = str(operation or "chat").strip().lower()[:64] or "chat"
    result = outcome if outcome in _ALLOWED_OUTCOMES else "failure"
    why = reason if reason in _ALLOWED_REASONS else "validation"
    payload = {
        "operation": op,
        "outcome": result,
        "reason": why,
        "latency_ms": max(0, min(int(latency_ms or 0), 3_600_000)),
    }
    if count is not None:
        payload["count"] = max(0, min(int(count or 0), 1_000_000))
    try:
        frappe.logger("aos.chat", allow_site=True).info(payload)
    except Exception:
        pass


@contextmanager
def chat_timing(operation: str) -> Iterator[dict[str, int]]:
    started = time.monotonic()
    state = {"latency_ms": 0}
    try:
        yield state
    finally:
        state["latency_ms"] = max(0, int((time.monotonic() - started) * 1000))
