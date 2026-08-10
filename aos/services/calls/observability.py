"""Production-safe structured Calls logging."""

from __future__ import annotations

import logging

_LOG = logging.getLogger("aos.calls")
_ALLOWED_OUTCOMES = {"success", "failure", "rejected", "conflict", "idempotent"}


def call_log(
    operation: str,
    *,
    outcome: str,
    reason: str = "none",
    count: int | None = None,
    latency_ms: int | None = None,
) -> None:
    """Emit bounded metadata only; never tokens, sessions, push tokens or secrets."""
    event = {
        "operation": str(operation or "unknown")[:64],
        "outcome": outcome if outcome in _ALLOWED_OUTCOMES else "failure",
        "reason": str(reason or "none")[:64],
    }
    if count is not None:
        try:
            event["count"] = max(0, min(int(count), 100000))
        except Exception:
            pass
    if latency_ms is not None:
        try:
            event["latency_ms"] = max(0, min(int(latency_ms), 300000))
        except Exception:
            pass
    _LOG.info("aos.calls %s", event)
    try:
        from aos.utils.metrics import record_call_event

        record_call_event(event=event["operation"], outcome=event["outcome"])
    except Exception:
        # Metrics must never affect the Calls business path.
        pass
