"""Low-cardinality Catalog logs and metrics without category IDs or labels."""

from __future__ import annotations

import json

import frappe

_ALLOWED_EVENTS = {
    "categories_read",
    "schema_read",
    "attribute_options_read",
    "configuration_changed",
    "configuration_rejected",
}
_ALLOWED_OUTCOMES = {"success", "not_found", "rejected", "failure"}


def catalog_log(event: str, *, outcome: str = "success", category_kind: str = "unknown") -> None:
    safe_event = event if event in _ALLOWED_EVENTS else "configuration_rejected"
    safe_outcome = outcome if outcome in _ALLOWED_OUTCOMES else "failure"
    safe_kind = category_kind if category_kind in {"group", "leaf", "unknown"} else "unknown"
    payload = {"event": safe_event, "outcome": safe_outcome, "category_kind": safe_kind}
    if safe_outcome != "success" or safe_event in {"configuration_changed", "configuration_rejected"}:
        try:
            frappe.logger("aos.catalog").info(
                json.dumps(payload, sort_keys=True, separators=(",", ":"))
            )
        except Exception:
            pass
    try:
        from aos.utils.metrics import record_catalog_event

        record_catalog_event(event=safe_event, outcome=safe_outcome)
    except Exception:
        pass
