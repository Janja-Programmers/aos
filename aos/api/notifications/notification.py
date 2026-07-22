"""
Notification APIs (implementation).

Handles:
- list_notifications
- mark_notification_read
- mark_all_notifications_read
- delete_notification
- clear_notifications
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.user_display import get_user_display

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


# HELPERS
def _serialize_notification(notification):
    actor_display = get_user_display(notification.actor) if notification.actor else None

    return {
        "id": notification.name,
        "type": notification.type,
        "title": notification.title,
        "body": notification.body,
        "actor": actor_display.get("user") if actor_display else None,
        "actor_display_name": (
            actor_display.get("display_name")
            if actor_display
            else None
        ),
        "actor_avatar": (
            actor_display.get("avatar")
            if actor_display
            else None
        ),
        "actor_is_deleted": (
            bool(actor_display.get("is_deleted"))
            if actor_display
            else False
        ),
        "actor_is_live": (
            bool(actor_display.get("is_live"))
            if actor_display and not bool(actor_display.get("is_deleted"))
            else False
        ),
        "actor_live_id": (
            actor_display.get("live_id")
            if actor_display and not bool(actor_display.get("is_deleted"))
            else None
        ),
        "actor_live_status": (
            actor_display.get("live_status")
            if actor_display and not bool(actor_display.get("is_deleted"))
            else None
        ),
        "payload": notification.payload or {},
        "is_read": notification.is_read,
        "created_at": notification.creation,
    }


def _resolve_category(value):
    """
    Validate and normalize the requested notification category.

    Returns:
        tuple:
            category
            notification types, or None for the "all" category
            validation error, or None
    """
    category = str(value or NOTIFICATION_CATEGORY_ALL).strip().lower()

    if category not in VALID_NOTIFICATION_CATEGORIES:
        allowed_categories = ", ".join(VALID_NOTIFICATION_CATEGORIES)

        return (
            None,
            None,
            fail(
                (
                    "Invalid notification category. "
                    f"Allowed values are: {allowed_categories}."
                ),
                error="VALIDATION_ERROR",
            ),
        )

    notification_types = NOTIFICATION_CATEGORY_TYPES.get(category)

    return category, notification_types, None


def _resolve_limit(value):
    """
    Validate and normalize the requested page size.
    """
    try:
        limit = int(value or NOTIFICATION_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return (
            None,
            fail(
                "limit must be a valid integer.",
                error="VALIDATION_ERROR",
            ),
        )

    if limit < 1 or limit > NOTIFICATION_MAX_LIMIT:
        return (
            None,
            fail(
                (
                    "limit must be between 1 and "
                    f"{NOTIFICATION_MAX_LIMIT}."
                ),
                error="VALIDATION_ERROR",
            ),
        )

    return limit, None


def _build_notification_filters(
    *,
    current_user: str,
    notification_types: tuple[str, ...] | None = None,
):
    """
    Build ownership-safe notification filters.

    Every query or bulk operation must always be restricted to the
    authenticated user.
    """
    filters = {
        "user": current_user,
    }

    if notification_types:
        filters["type"] = ("in", list(notification_types))

    return filters


def _get_cursor_creation(
    *,
    notification_id: str,
    current_user: str,
    notification_types: tuple[str, ...] | None,
):
    """
    Resolve a pagination cursor.

    The cursor must:
    - Belong to the authenticated user
    - Belong to the currently selected category
    """
    filters = _build_notification_filters(
        current_user=current_user,
        notification_types=notification_types,
    )
    filters["name"] = notification_id

    return frappe.db.get_value(
        "AOS Notification",
        filters,
        "creation",
    )


# LIST NOTIFICATIONS
def list_notifications_impl(**kwargs):
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

    category, notification_types, category_err = _resolve_category(
        kwargs.get("category")
    )
    if category_err:
        return category_err

    limit, limit_err = _resolve_limit(kwargs.get("limit"))
    if limit_err:
        return limit_err

    before = str(kwargs.get("before") or "").strip() or None

    try:
        filters = _build_notification_filters(
            current_user=current_user,
            notification_types=notification_types,
        )

        if before:
            before_creation = _get_cursor_creation(
                notification_id=before,
                current_user=current_user,
                notification_types=notification_types,
            )

            if not before_creation:
                return fail(
                    (
                        "Invalid 'before' notification for the "
                        "selected category."
                    ),
                    error="VALIDATION_ERROR",
                )

            filters["creation"] = ("<", before_creation)

        rows = frappe.get_all(
            "AOS Notification",
            filters=filters,
            fields=[
                "name",
                "type",
                "title",
                "body",
                "actor",
                "payload",
                "is_read",
                "creation",
            ],
            order_by="creation desc",
            limit_page_length=limit,
        )

        if not rows:
            return ok(
                "Notifications fetched.",
                data={
                    "category": category,
                    "items": [],
                    "next_cursor": None,
                },
            )

        items = [
            _serialize_notification(notification)
            for notification in rows
        ]

        next_cursor = rows[-1].name

        return ok(
            "Notifications fetched.",
            data={
                "category": category,
                "items": items,
                "next_cursor": next_cursor,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Notifications Failed",
        )

        return fail(
            "Failed to fetch notifications.",
            error="INTERNAL_ERROR",
        )


# MARK SINGLE READ
def mark_notification_read_impl(**kwargs):
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

    notification_id = str(
        kwargs.get("notification_id") or ""
    ).strip()

    if not notification_id:
        return fail(
            "notification_id is required.",
            error="VALIDATION_ERROR",
        )

    try:
        exists = frappe.db.exists(
            "AOS Notification",
            {
                "name": notification_id,
                "user": current_user,
            },
        )

        if not exists:
            return fail(
                "Notification not found.",
                error="NOT_FOUND",
            )

        frappe.db.set_value(
            "AOS Notification",
            notification_id,
            "is_read",
            1,
            update_modified=False,
        )

        return ok(
            "Notification marked as read.",
            data={
                "notification_id": notification_id,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Mark Notification Read Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to update notification.",
            error="INTERNAL_ERROR",
        )


# MARK ALL READ
def mark_all_notifications_read_impl(**kwargs):
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
        frappe.db.sql(
            """
            UPDATE `tabAOS Notification`
            SET is_read = 1
            WHERE user = %s
              AND is_read = 0
            """,
            (current_user,),
        )

        return ok("All notifications marked as read.")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Mark All Notifications Read Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to update notifications.",
            error="INTERNAL_ERROR",
        )


# DELETE SINGLE NOTIFICATION
def delete_notification_impl(**kwargs):
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

    notification_id = str(
        kwargs.get("notification_id") or ""
    ).strip()

    if not notification_id:
        return fail(
            "notification_id is required.",
            error="VALIDATION_ERROR",
        )

    try:
        exists = frappe.db.exists(
            "AOS Notification",
            {
                "name": notification_id,
                "user": current_user,
            },
        )

        if not exists:
            return fail(
                "Notification not found.",
                error="NOT_FOUND",
            )

        frappe.delete_doc(
            "AOS Notification",
            notification_id,
            ignore_permissions=True,
        )

        return ok(
            "Notification deleted.",
            data={
                "notification_id": notification_id,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Delete Notification Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to delete notification.",
            error="INTERNAL_ERROR",
        )


# CLEAR NOTIFICATIONS
def clear_notifications_impl(**kwargs):
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

    category, notification_types, category_err = _resolve_category(
        kwargs.get("category")
    )
    if category_err:
        return category_err

    try:
        filters = _build_notification_filters(
            current_user=current_user,
            notification_types=notification_types,
        )

        deleted_count = frappe.db.count(
            "AOS Notification",
            filters=filters,
        )

        if deleted_count:
            frappe.db.delete(
                "AOS Notification",
                filters,
            )

        message = (
            "Notifications cleared."
            if deleted_count
            else "No notifications to clear."
        )

        return ok(
            message,
            data={
                "category": category,
                "deleted_count": deleted_count,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Clear Notifications Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to clear notifications.",
            error="INTERNAL_ERROR",
        )
