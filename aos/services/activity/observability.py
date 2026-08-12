"""Production-safe Activity Center observability."""

from __future__ import annotations

import frappe

_ALLOWED = frozenset({"event", "outcome", "activity_id", "activity_group", "activity_type", "count"})


def activity_log(event: str, *, outcome: str = "success", **fields) -> None:
    payload = {"event": str(event or "activity"), "outcome": str(outcome or "unknown")}
    for key, value in fields.items():
        if key in _ALLOWED and key not in {"event", "outcome"} and value not in (None, ""):
            payload[key] = value
    try:
        frappe.logger("aos.activity", allow_site=True).info("activity_event %s", payload)
    except Exception:
        pass
