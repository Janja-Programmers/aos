"""Post-commit Chat realtime and notification helpers."""

from __future__ import annotations

from collections.abc import Callable

import frappe


def after_commit(callback: Callable[[], None]) -> None:
    """Run side effects only after commit; failure never invalidates persisted Chat state."""
    manager = getattr(frappe.db, "after_commit", None)
    if manager is not None and hasattr(manager, "add"):
        manager.add(callback)
        return
    # Fallback is mainly for isolated tests/mocks that do not expose transaction
    # callbacks. Real Frappe requests use after_commit.add.
    try:
        callback()
    except Exception:
        frappe.log_error("Chat post-commit callback failed.", "AOS Chat post-commit failure")


def publish_after_commit(*, event: str, message: dict, user: str) -> None:
    payload = dict(message or {})

    def _publish() -> None:
        try:
            frappe.publish_realtime(event=event, message=payload, user=user)
        except Exception:
            frappe.log_error("Chat realtime publish failed.", "AOS Chat realtime failure")

    after_commit(_publish)
