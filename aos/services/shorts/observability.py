"""Privacy-safe Shorts structured logging.

Never pass account IDs, Short IDs, object keys, URLs, captions, comments,
search text, cursors, tokens, or exception tracebacks to these helpers.
"""

from __future__ import annotations

from typing import Any

import frappe

_ALLOWED_OUTCOMES = {"success", "noop", "rejected", "conflict", "failure"}


def shorts_log(operation: str, *, outcome: str, reason: str = "none", **metrics: Any) -> None:
    safe_operation = "".join(ch for ch in str(operation or "unknown").lower() if ch.isalnum() or ch in "_-")[:48]
    safe_outcome = outcome if outcome in _ALLOWED_OUTCOMES else "failure"
    safe_reason = "".join(ch for ch in str(reason or "none").lower() if ch.isalnum() or ch in "_-")[:48]
    safe_metrics = {
        key: value
        for key, value in metrics.items()
        if key in {"count", "limit", "duration_ms", "page_size", "attempt"}
        and isinstance(value, (int, float))
    }
    frappe.logger("aos.shorts", allow_site=True).info(
        "shorts_operation operation=%s outcome=%s reason=%s metrics=%s",
        safe_operation,
        safe_outcome,
        safe_reason,
        safe_metrics,
    )
