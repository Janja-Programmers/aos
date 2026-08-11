"""PII-safe Verification operational logging."""

from __future__ import annotations

import json
from typing import Any

import frappe

from aos.services.accounts.identity import public_account_id_for_user

_ALLOWED_EVENTS = {
    "verification.created",
    "verification.submitted",
    "verification.resubmitted",
    "verification.reviewing",
    "verification.approved",
    "verification.rejected",
    "verification.revoked",
    "verification.document.attached",
    "verification.document.released",
    "verification.notification.enqueued",
    "verification.access.denied",
}


def verification_log(
    event: str,
    *,
    user: str | None = None,
    verification_id: str | None = None,
    status: Any = None,
    outcome: str = "success",
    count: int | None = None,
) -> None:
    """Log bounded identifiers and lifecycle metadata, never submitted PII."""

    safe_event = event if event in _ALLOWED_EVENTS else "verification.access.denied"
    payload = {
        "event": safe_event,
        "verification_id": str(verification_id or "")[:64] or None,
        "account_id": public_account_id_for_user(user) if user else None,
        "status": str(status or "")[:32] or None,
        "outcome": str(outcome or "")[:24],
        "count": max(0, int(count or 0)),
    }
    try:
        frappe.logger("aos.verification", allow_site=True).info(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
    except Exception:
        pass
