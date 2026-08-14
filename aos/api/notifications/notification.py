"""Authenticated AOS in-app Notification Center APIs."""

from __future__ import annotations

import uuid
from typing import Any

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.user_display import get_user_display_map
from aos.services.accounts.http import set_private_no_store
from aos.services.notifications.contracts import sanitize_public_payload
from aos.services.notifications.validation import (
    NotificationInputError,
    reject_unknown_fields,
    require_identifier,
)

from .constants import (
    CLEAR_NOTIFICATIONS_LIMIT_PER_MINUTE_PER_USER,
    DELETE_NOTIFICATION_LIMIT_PER_MINUTE_PER_USER,
    LIST_NOTIFICATIONS_LIMIT_PER_MINUTE_PER_USER,
    MARK_ALL_NOTIFICATIONS_READ_LIMIT_PER_MINUTE_PER_USER,
    MARK_NOTIFICATION_READ_LIMIT_PER_MINUTE_PER_USER,
    NOTIFICATION_CATEGORY_ALL,
    NOTIFICATION_CATEGORY_TYPES,
    NOTIFICATION_DEFAULT_LIMIT,
    NOTIFICATION_MAX_LIMIT,
    VALID_NOTIFICATION_CATEGORIES,
)


def _input_error(message: str):
    return fail(message, error="VALIDATION_ERROR")


def _rollback_savepoint(savepoint: str) -> None:
    try:
        frappe.db.rollback(save_point=savepoint)
    except Exception:
        pass


def _resolve_category(value):
    category = str(value or NOTIFICATION_CATEGORY_ALL).strip().lower()
    if category not in VALID_NOTIFICATION_CATEGORIES:
        return None, None, _input_error(
            "Invalid notification category. Allowed values are: "
            f"{', '.join(VALID_NOTIFICATION_CATEGORIES)}."
        )
    return category, NOTIFICATION_CATEGORY_TYPES.get(category), None


def _resolve_limit(value):
    try:
        limit = int(value or NOTIFICATION_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return None, _input_error("limit must be a valid integer.")
    if limit < 1 or limit > NOTIFICATION_MAX_LIMIT:
        return None, _input_error(f"limit must be between 1 and {NOTIFICATION_MAX_LIMIT}.")
    return limit, None


def _build_notification_filters(*, current_user: str, notification_types: tuple[str, ...] | None = None):
    filters: dict[str, Any] = {"user": current_user}
    if notification_types:
        filters["type"] = ("in", list(notification_types))
    return filters


def _get_cursor(*, notification_id: str, current_user: str, notification_types: tuple[str, ...] | None):
    filters = _build_notification_filters(
        current_user=current_user,
        notification_types=notification_types,
    )
    filters["name"] = notification_id
    return frappe.db.get_value("AOS Notification", filters, ["creation", "name"], as_dict=True)


def _list_rows(
    *,
    current_user: str,
    notification_types: tuple[str, ...] | None,
    cursor: dict[str, Any] | None,
    limit: int,
):
    conditions = ["`user` = %s"]
    params: list[Any] = [current_user]
    if notification_types:
        placeholders = ", ".join(["%s"] * len(notification_types))
        conditions.append(f"`type` IN ({placeholders})")
        params.extend(notification_types)
    if cursor:
        conditions.append("(`creation` < %s OR (`creation` = %s AND `name` < %s))")
        params.extend([cursor["creation"], cursor["creation"], cursor["name"]])
    params.append(limit + 1)
    return frappe.db.sql(
        f"""
        SELECT
            `name`, `type`, `title`, `body`, `actor`, `payload`, `is_read`, `creation`
        FROM `tabAOS Notification`
        WHERE {' AND '.join(conditions)}
        ORDER BY `creation` DESC, `name` DESC
        LIMIT %s
        """,
        tuple(params),
        as_dict=True,
    )


def _serialize_notifications(rows: list[Any]) -> list[dict[str, Any]]:
    actors = {str(row.actor).strip() for row in rows if getattr(row, "actor", None)}
    actor_map = get_user_display_map(actors) if actors else {}
    items: list[dict[str, Any]] = []
    for notification in rows:
        actor_display = actor_map.get(notification.actor) if notification.actor else None
        actor_deleted = bool(actor_display.get("is_deleted")) if actor_display else False
        items.append(
            {
                "id": notification.name,
                "type": notification.type,
                "title": notification.title,
                "body": notification.body,
                "actor": actor_display.get("user") if actor_display else None,
                "actor_display_name": actor_display.get("display_name") if actor_display else None,
                "actor_avatar": actor_display.get("avatar") if actor_display else None,
                "actor_is_deleted": actor_deleted,
                "actor_is_live": bool(actor_display.get("is_live")) if actor_display and not actor_deleted else False,
                "actor_live_id": actor_display.get("live_id") if actor_display and not actor_deleted else None,
                "actor_live_status": actor_display.get("live_status") if actor_display and not actor_deleted else None,
                "payload": sanitize_public_payload(notification.type, notification.payload or {}),
                "is_read": bool(int(notification.is_read or 0)),
                "created_at": notification.creation,
            }
        )
    return items


def list_notifications_impl(**kwargs):
    set_private_no_store()
    current_user, err = require_login()
    if err:
        return err
    rl = rate_limit(
        key=f"aos:notifications:list:user:{current_user}",
        ttl_seconds=60,
        limit=LIST_NOTIFICATIONS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl
    try:
        reject_unknown_fields(kwargs, allowed={"category", "limit", "before"})
    except NotificationInputError as exc:
        return _input_error(str(exc))
    category, notification_types, category_err = _resolve_category(kwargs.get("category"))
    if category_err:
        return category_err
    limit, limit_err = _resolve_limit(kwargs.get("limit"))
    if limit_err:
        return limit_err
    before = str(kwargs.get("before") or "").strip() or None
    if before and len(before) > 180:
        return _input_error("Invalid 'before' notification for the selected category.")

    try:
        cursor = None
        if before:
            cursor = _get_cursor(
                notification_id=before,
                current_user=current_user,
                notification_types=notification_types,
            )
            if not cursor:
                return _input_error("Invalid 'before' notification for the selected category.")
        rows = _list_rows(
            current_user=current_user,
            notification_types=notification_types,
            cursor=cursor,
            limit=limit,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        return ok(
            "Notifications fetched.",
            data={
                "category": category,
                "items": _serialize_notifications(page),
                "next_cursor": page[-1].name if has_more and page else None,
            },
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS List Notifications Failed")
        return fail("Failed to fetch notifications.", error="INTERNAL_ERROR")


def mark_notification_read_impl(**kwargs):
    set_private_no_store()
    current_user, err = require_login()
    if err:
        return err
    rl = rate_limit(
        key=f"aos:notifications:read:user:{current_user}",
        ttl_seconds=60,
        limit=MARK_NOTIFICATION_READ_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl
    try:
        reject_unknown_fields(kwargs, allowed={"notification_id"})
        notification_id = require_identifier(kwargs.get("notification_id"), field="notification_id")
    except NotificationInputError as exc:
        return _input_error(str(exc))
    try:
        exists = frappe.db.exists("AOS Notification", {"name": notification_id, "user": current_user})
        if not exists:
            return fail("Notification not found.", error="NOT_FOUND")
        frappe.db.set_value(
            "AOS Notification",
            {"name": notification_id, "user": current_user, "is_read": 0},
            "is_read",
            1,
            update_modified=False,
        )
        return ok("Notification marked as read.", data={"notification_id": notification_id})
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Mark Notification Read Failed")
        return fail("Failed to update notification.", error="INTERNAL_ERROR")


def mark_all_notifications_read_impl(**kwargs):
    set_private_no_store()
    current_user, err = require_login()
    if err:
        return err
    rl = rate_limit(
        key=f"aos:notifications:read_all:user:{current_user}",
        ttl_seconds=60,
        limit=MARK_ALL_NOTIFICATIONS_READ_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl
    try:
        reject_unknown_fields(kwargs, allowed=set())
    except NotificationInputError as exc:
        return _input_error(str(exc))
    try:
        frappe.db.sql(
            "UPDATE `tabAOS Notification` SET is_read = 1 WHERE user = %s AND is_read = 0",
            (current_user,),
        )
        return ok("All notifications marked as read.")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Mark All Notifications Read Failed")
        return fail("Failed to update notifications.", error="INTERNAL_ERROR")


def delete_notification_impl(**kwargs):
    set_private_no_store()
    current_user, err = require_login()
    if err:
        return err
    rl = rate_limit(
        key=f"aos:notifications:delete:user:{current_user}",
        ttl_seconds=60,
        limit=DELETE_NOTIFICATION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl
    try:
        reject_unknown_fields(kwargs, allowed={"notification_id"})
        notification_id = require_identifier(kwargs.get("notification_id"), field="notification_id")
    except NotificationInputError as exc:
        return _input_error(str(exc))
    savepoint = f"aos_notification_delete_{uuid.uuid4().hex[:10]}"
    try:
        exists = frappe.db.exists("AOS Notification", {"name": notification_id, "user": current_user})
        if not exists:
            return fail("Notification not found.", error="NOT_FOUND")
        frappe.db.savepoint(savepoint)
        frappe.db.delete("AOS Notification", {"name": notification_id, "user": current_user})
        return ok("Notification deleted.", data={"notification_id": notification_id})
    except Exception:
        _rollback_savepoint(savepoint)
        frappe.log_error(frappe.get_traceback(), "AOS Delete Notification Failed")
        return fail("Failed to delete notification.", error="INTERNAL_ERROR")


def clear_notifications_impl(**kwargs):
    set_private_no_store()
    current_user, err = require_login()
    if err:
        return err
    rl = rate_limit(
        key=f"aos:notifications:clear:user:{current_user}",
        ttl_seconds=60,
        limit=CLEAR_NOTIFICATIONS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl
    try:
        reject_unknown_fields(kwargs, allowed={"category"})
    except NotificationInputError as exc:
        return _input_error(str(exc))
    category, notification_types, category_err = _resolve_category(kwargs.get("category"))
    if category_err:
        return category_err
    savepoint = f"aos_notification_clear_{uuid.uuid4().hex[:10]}"
    try:
        filters = _build_notification_filters(
            current_user=current_user,
            notification_types=notification_types,
        )
        deleted_count = frappe.db.count("AOS Notification", filters=filters)
        if deleted_count:
            frappe.db.savepoint(savepoint)
            frappe.db.delete("AOS Notification", filters)
        return ok(
            "Notifications cleared." if deleted_count else "No notifications to clear.",
            data={"category": category, "deleted_count": deleted_count},
        )
    except Exception:
        _rollback_savepoint(savepoint)
        frappe.log_error(frappe.get_traceback(), "AOS Clear Notifications Failed")
        return fail("Failed to clear notifications.", error="INTERNAL_ERROR")
