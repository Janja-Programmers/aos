"""Privacy-safe structured observability for Live operations."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Iterator

import frappe

_ALLOWED_OUTCOMES = {"success", "partial", "idempotent", "duplicate", "rejected", "conflict", "failure"}
_ALLOWED_REASONS = {
    "none", "validation", "unknown_field", "alias_conflict", "identifier", "access",
    "state", "duplicate", "rate_limit", "dependency", "cursor", "signature", "replay", "internal",
    "schema", "processing", "bounded_batch", "created", "already_exists", "deleted", "removed",
    "listed", "not_found", "timeout", "authentication", "unavailable", "invalid_room",
    "invalid_participant", "live_ended", "already_ended", "stale_event", "unknown_room",
    "participant_seen", "participant_denied", "participant_left", "already_left",
    "untracked_participant", "missing_participant", "inactive_room", "room_observed",
    "unsupported_event", "already_processed", "ended_during_create", "host_unavailable",
    "room_finished_before_activation", "state_changed", "starting", "live", "ended", "failed",
    "activate", "cleanup", "active",
}


def live_log(operation: str, *, outcome: str, reason: str = "none", latency_ms: int = 0, count: int | None = None) -> None:
    payload = {
        "operation": str(operation or "unknown")[:48],
        "outcome": outcome if outcome in _ALLOWED_OUTCOMES else "failure",
        "reason": reason if reason in _ALLOWED_REASONS else "internal",
        "latency_ms": max(0, min(int(latency_ms or 0), 3_600_000)),
        "count": None if count is None else max(0, min(int(count), 1_000_000)),
    }
    try:
        frappe.logger("aos.live", allow_site=True).info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except Exception:
        pass


@contextmanager
def observe(operation: str) -> Iterator[dict[str, Any]]:
    started = time.monotonic()
    state: dict[str, Any] = {"outcome": "success", "reason": "none"}
    try:
        yield state
    except Exception:
        state.setdefault("outcome", "failure")
        state.setdefault("reason", "internal")
        raise
    finally:
        live_log(
            operation,
            outcome=str(state.get("outcome") or "success"),
            reason=str(state.get("reason") or "none"),
            latency_ms=int((time.monotonic() - started) * 1000),
            count=state.get("count"),
        )
