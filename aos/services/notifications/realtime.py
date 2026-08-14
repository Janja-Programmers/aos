"""Recipient-scoped post-commit Notification Center realtime synchronization.

Persistent Notification records remain the source of truth. Realtime is only a
foreground transport hint: clients must reconcile from the inbox after reconnect
and must tolerate duplicate events.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

import frappe

from aos.services.notifications.observability import notification_log
from aos.services.notifications.policy import persistent_notification_suppression_reason
from aos.services.notifications.repository import get_unread_count
from aos.services.notifications.serializers import serialize_notification

EVENT_NOTIFICATION_CENTER = "aos_notification_center"
REALTIME_VERSION = 1
RealtimeAction = Literal["created", "read", "read_all", "deleted", "cleared"]


def _after_commit(callback: Callable[[], None]) -> None:
    def _safe_callback() -> None:
        try:
            callback()
        except Exception:
            try:
                frappe.log_error(
                    "Notification realtime post-commit callback failed.",
                    "AOS Notification realtime failure",
                )
            except Exception:
                pass

    manager = getattr(frappe.db, "after_commit", None)
    if manager is not None and hasattr(manager, "add"):
        try:
            manager.add(_safe_callback)
        except Exception:
            # Never publish before commit merely because callback registration
            # failed; REST inbox reconciliation remains authoritative.
            try:
                frappe.log_error(
                    "Notification realtime callback registration failed.",
                    "AOS Notification realtime failure",
                )
            except Exception:
                pass
        return

    # Compatibility for isolated tests/mocks. Real Frappe requests use the
    # transaction callback manager above.
    _safe_callback()


def _publish(*, user: str, message: dict[str, Any]) -> None:
    try:
        frappe.publish_realtime(
            event=EVENT_NOTIFICATION_CENTER,
            message=message,
            user=user,
        )
        notification_log(
            "notification.realtime_published",
            notification_id=message.get("notification_id")
            or (message.get("notification") or {}).get("id"),
            notification_type=(message.get("notification") or {}).get("type"),
            outcome="published",
            reason=str(message.get("action") or "")[:40],
        )
    except Exception as exc:
        notification_log(
            "notification.realtime_failed",
            notification_id=message.get("notification_id")
            or (message.get("notification") or {}).get("id"),
            notification_type=(message.get("notification") or {}).get("type"),
            outcome="failed",
            reason=exc.__class__.__name__,
        )
        try:
            frappe.log_error(
                "Notification realtime publish failed.",
                "AOS Notification realtime failure",
            )
        except Exception:
            pass


def publish_created_after_commit(*, user: str, notification_id: str) -> None:
    """Publish a newly committed public-safe notification to one recipient."""
    user = str(user or "").strip()
    notification_id = str(notification_id or "").strip()
    if not user or not notification_id:
        return

    def _callback() -> None:
        row = frappe.db.get_value(
            "AOS Notification",
            {"name": notification_id, "user": user},
            [
                "name",
                "type",
                "title",
                "body",
                "actor",
                "payload",
                "is_read",
                "creation",
            ],
            as_dict=True,
        )
        if not row:
            return
        reason = persistent_notification_suppression_reason(
            user=user,
            notification_type=str(row.get("type") or ""),
            actor=str(row.get("actor") or "").strip() or None,
        )
        if reason:
            notification_log(
                "notification.realtime_suppressed",
                notification_id=notification_id,
                notification_type=str(row.get("type") or ""),
                outcome="suppressed",
                reason=reason,
            )
            return
        notification = serialize_notification(row)
        if not notification:
            return
        _publish(
            user=user,
            message={
                "version": REALTIME_VERSION,
                "action": "created",
                "notification": notification,
                "unread_count": get_unread_count(user),
            },
        )

    _after_commit(_callback)


def publish_state_after_commit(
    *,
    user: str,
    action: RealtimeAction,
    notification_id: str | None = None,
    category: str | None = None,
    deleted_count: int | None = None,
) -> None:
    """Synchronize owner mutations across active tabs/devices after commit."""
    user = str(user or "").strip()
    if not user or action == "created":
        return

    safe_notification_id = str(notification_id or "").strip() or None
    safe_category = str(category or "").strip() or None
    safe_deleted_count = None
    if deleted_count is not None:
        try:
            safe_deleted_count = max(0, int(deleted_count))
        except (TypeError, ValueError):
            safe_deleted_count = 0

    def _callback() -> None:
        message: dict[str, Any] = {
            "version": REALTIME_VERSION,
            "action": action,
            "unread_count": get_unread_count(user),
        }
        if safe_notification_id:
            message["notification_id"] = safe_notification_id
        if safe_category:
            message["category"] = safe_category
        if safe_deleted_count is not None:
            message["deleted_count"] = safe_deleted_count
        _publish(user=user, message=message)

    _after_commit(_callback)
