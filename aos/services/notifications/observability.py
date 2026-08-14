"""Privacy-safe structured logging for Notification infrastructure."""

from __future__ import annotations

from typing import Any

import frappe


def notification_log(event: str, **fields: Any) -> None:
    """Emit bounded metadata only; never payload bodies, raw tokens, or secrets."""
    safe: dict[str, Any] = {"event": str(event or "notification.event")[:96]}
    allowed = {
        "notification_id",
        "job_id",
        "account_id",
        "category",
        "notification_type",
        "delivery_kind",
        "platform",
        "registration_kind",
        "attempt",
        "outcome",
        "reason",
        "count",
        "token_fingerprint",
        "provider",
        "error_code",
        "status",
    }
    for key in allowed:
        if key not in fields or fields[key] is None:
            continue
        value = fields[key]
        if isinstance(value, (int, float, bool)):
            safe[key] = value
        else:
            safe[key] = str(value)[:180]
    try:
        frappe.logger("aos.notifications", allow_site=True).info("notification_event=%s", safe)
    except Exception:
        pass
