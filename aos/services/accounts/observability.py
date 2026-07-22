"""Bounded Accounts logs and metrics without PII."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable

import frappe

_ALLOWED_EVENTS = {
    "account.profile.read",
    "account.profile.updated",
    "account.preference.updated",
    "account.avatar.replaced",
    "account.avatar.removed",
    "account.bootstrap.completed",
    "account.bootstrap.failed",
    "account.deactivation.requested",
    "account.deactivated",
    "account.deletion.requested",
    "account.deleted",
    "account.restored",
}
_ALLOWED_OUTCOMES = {"success", "rejected", "failure"}
_ALLOWED_FIELDS = {"display_name", "legal_name", "phone", "date_of_birth", "gender", "bio", "location", "avatar"}


def _opaque(user: str) -> str:
    return hashlib.sha256(str(user or "").encode("utf-8")).hexdigest()[:16]


def account_log(
    event: str,
    *,
    user: str,
    outcome: str = "success",
    changed_fields: Iterable[str] | None = None,
    failure_category: str | None = None,
) -> None:
    safe_event = event if event in _ALLOWED_EVENTS else "account.bootstrap.failed"
    safe_outcome = outcome if outcome in _ALLOWED_OUTCOMES else "failure"
    payload = {
        "event": safe_event,
        "account_ref": _opaque(user),
        "outcome": safe_outcome,
        "changed_fields": sorted(set(changed_fields or ()) & _ALLOWED_FIELDS),
        "failure_category": str(failure_category or "")[:48] or None,
    }
    try:
        frappe.logger("aos.accounts").info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except Exception:
        pass
    try:
        from aos.utils.metrics import record_account_event

        record_account_event(event=safe_event, outcome=safe_outcome)
    except Exception:
        pass
