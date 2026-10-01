"""Post-commit Chat realtime and notification helpers."""

from __future__ import annotations

from collections.abc import Callable

import frappe


def after_commit(callback: Callable[[], None]) -> None:
    """Run side effects only after commit; failure never invalidates persisted Chat state."""
    manager = getattr(frappe.db, "after_commit", None)
    if manager is None or not callable(getattr(manager, "add", None)):
        # Realtime is a best-effort hint. Never publish inside an open transaction:
        # clients reconcile from the durable conversation state on reconnect.
        frappe.log_error(
            "Chat transaction callback registration unavailable; realtime suppressed.",
            "AOS Chat post-commit failure",
        )
        return
    manager.add(callback)


def publish_after_commit(*, event: str, message: dict, user: str) -> None:
    payload = dict(message or {})

    def _publish() -> None:
        try:
            frappe.publish_realtime(event=event, message=payload, user=user)
        except Exception:
            frappe.log_error("Chat realtime publish failed.", "AOS Chat realtime failure")

    after_commit(_publish)
