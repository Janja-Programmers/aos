"""Public-safe serializers for Notification Center records."""

from __future__ import annotations

from typing import Any

from aos.api.shared.user_display import get_user_display_map
from aos.services.notifications.contracts import sanitize_public_payload


def _value(row: Any, field: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(field, default)
    return getattr(row, field, default)


def serialize_notifications(rows: list[Any]) -> list[dict[str, Any]]:
    """Serialize Notification records without internal User/provider metadata."""
    actors = {
        str(actor).strip()
        for row in rows
        if (actor := _value(row, "actor")) and str(actor).strip()
    }
    try:
        actor_map = get_user_display_map(actors) if actors else {}
    except Exception:
        actor_map = {}

    items: list[dict[str, Any]] = []
    for notification in rows:
        actor = str(_value(notification, "actor") or "").strip() or None
        actor_display = actor_map.get(actor) if actor else None
        actor_deleted = bool(actor_display.get("is_deleted")) if actor_display else False
        notification_type = str(_value(notification, "type") or "").strip()
        items.append(
            {
                "id": _value(notification, "name"),
                "type": notification_type,
                "title": _value(notification, "title"),
                "body": _value(notification, "body"),
                "actor": actor_display.get("account_id") if actor_display else None,
                "actor_display_name": actor_display.get("display_name") if actor_display else None,
                "actor_avatar": actor_display.get("avatar") if actor_display else None,
                "actor_is_deleted": actor_deleted,
                "actor_is_live": bool(actor_display.get("is_live"))
                if actor_display and not actor_deleted
                else False,
                "actor_live_id": actor_display.get("live_id")
                if actor_display and not actor_deleted
                else None,
                "actor_live_status": actor_display.get("live_status")
                if actor_display and not actor_deleted
                else None,
                "payload": sanitize_public_payload(
                    notification_type,
                    _value(notification, "payload") or {},
                ),
                "is_read": bool(int(_value(notification, "is_read", 0) or 0)),
                "created_at": _value(notification, "creation"),
            }
        )
    return items


def serialize_notification(row: Any) -> dict[str, Any] | None:
    items = serialize_notifications([row])
    return items[0] if items else None
