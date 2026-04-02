"""
Notification APIs (implementation).

Handles:
- list_notifications
- mark_notification_read
- mark_all_notifications_read
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import (
    LIST_NOTIFICATIONS_LIMIT_PER_MINUTE_PER_USER,
    MARK_NOTIFICATION_READ_LIMIT_PER_MINUTE_PER_USER,
    MARK_ALL_NOTIFICATIONS_READ_LIMIT_PER_MINUTE_PER_USER,
)


# HELPERS
def _serialize_notification(n):
    return {
        "id": n.name,
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "actor": n.actor,
        "payload": n.payload or {},
        "is_read": n.is_read,
        "created_at": n.creation,
    }


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

    limit = int(kwargs.get("limit") or 20)
    before = kwargs.get("before")

    try:
        filters = {"user": current_user}

        if before:
            before_creation = frappe.db.get_value(
                "AOS Notification",
                before,
                "creation",
            )

            if not before_creation:
                return fail("Invalid 'before' notification.", code="VALIDATION_ERROR")

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
            return ok("Notifications fetched.", data={"items": [], "next_cursor": None})

        items = [_serialize_notification(r) for r in rows]

        next_cursor = rows[-1].name

        return ok(
            "Notifications fetched.",
            data={
                "items": items,
                "next_cursor": next_cursor,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Notifications Failed",
        )
        return fail("Failed to fetch notifications.", code="INTERNAL_ERROR")


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

    notification_id = kwargs.get("notification_id")

    if not notification_id:
        return fail("notification_id is required.", code="VALIDATION_ERROR")

    try:
        exists = frappe.db.exists(
            "AOS Notification",
            {"name": notification_id, "user": current_user},
        )

        if not exists:
            return fail("Notification not found.", code="NOT_FOUND")

        frappe.db.set_value(
            "AOS Notification",
            notification_id,
            "is_read",
            1,
            update_modified=False,
        )

        return ok("Notification marked as read.")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Mark Notification Read Failed",
        )
        frappe.db.rollback()
        return fail("Failed to update notification.", code="INTERNAL_ERROR")


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
            WHERE user = %s AND is_read = 0
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
        return fail("Failed to update notifications.", code="INTERNAL_ERROR")
