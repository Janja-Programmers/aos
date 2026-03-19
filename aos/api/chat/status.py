"""
Status APIs (implementation).

Handles:
- mark_delivered
- mark_read
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import (
    MARK_DELIVERED_LIMIT_PER_MINUTE_PER_USER,
    MARK_READ_LIMIT_PER_MINUTE_PER_USER,
)

from .presence import publish_presence_update_to_peers


# Helpers
def _get_conversation_row(conv_id: str):
    return frappe.db.get_value(
        "AOS Conversation",
        conv_id,
        ["name", "participant_1", "participant_2"],
        as_dict=True,
    )


def _validate_participant(conv, user: str) -> bool:
    if not conv:
        return False
    return user in (conv.participant_1, conv.participant_2)


def _get_other_user(conv, user: str) -> str | None:
    if not conv:
        return None
    if conv.participant_1 == user:
        return conv.participant_2
    if conv.participant_2 == user:
        return conv.participant_1
    return None


def _get_unread_field_for_reader(conv, reader: str) -> str:
    return "unread_count_1" if conv.participant_1 == reader else "unread_count_2"


# mark_delivered
def mark_delivered_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:delivered:user:{current_user}",
        ttl_seconds=60,
        limit=MARK_DELIVERED_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")
    if not conv_id:
        return fail("conversation_id is required.", code="VALIDATION_ERROR")

    try:
        conv = _get_conversation_row(conv_id)
        if not conv:
            return fail("Conversation not found.", code="NOT_FOUND")

        if not _validate_participant(conv, current_user):
            return fail("Not allowed.", code="PERMISSION_DENIED")

        other_user = _get_other_user(conv, current_user)
        if not other_user:
            return fail("Conversation participant not found.", code="NOT_FOUND")

        now = now_datetime()

        # Mark only messages sent TO current_user and not yet delivered
        frappe.db.sql(
            """
            UPDATE `tabAOS Message`
            SET delivered_to_receiver_at = %s
            WHERE conversation = %s
              AND sender = %s
              AND delivered_to_receiver_at IS NULL
            """,
            (now, conv_id, other_user),
        )

        updated_count = frappe.db.sql("SELECT ROW_COUNT() AS count", as_dict=True)[0].count or 0

        # Notify sender that receiver has now received messages in this conversation
        if updated_count > 0:
            frappe.publish_realtime(
                event="aos_message_status",
                message={
                    "conversation_id": conv_id,
                    "status": "delivered",
                    "delivered_at": now,
                },
                user=other_user,
            )

        publish_presence_update_to_peers(current_user)

        return ok(
            "Messages marked as delivered.",
            data={"updated_count": updated_count},
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Mark Delivered Failed",
        )
        frappe.db.rollback()
        return fail("Failed to update delivered status.", code="INTERNAL_ERROR")


# mark_read
def mark_read_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:read:user:{current_user}",
        ttl_seconds=60,
        limit=MARK_READ_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")
    if not conv_id:
        return fail("conversation_id is required.", code="VALIDATION_ERROR")

    try:
        conv = _get_conversation_row(conv_id)
        if not conv:
            return fail("Conversation not found.", code="NOT_FOUND")

        if not _validate_participant(conv, current_user):
            return fail("Not allowed.", code="PERMISSION_DENIED")

        other_user = _get_other_user(conv, current_user)
        if not other_user:
            return fail("Conversation participant not found.", code="NOT_FOUND")

        unread_field = _get_unread_field_for_reader(conv, current_user)
        now = now_datetime()

        # Mark unread incoming messages as read
        frappe.db.sql(
            """
            UPDATE `tabAOS Message`
            SET read_by_receiver_at = %s
            WHERE conversation = %s
              AND sender = %s
              AND read_by_receiver_at IS NULL
            """,
            (now, conv_id, other_user),
        )

        updated_count = frappe.db.sql("SELECT ROW_COUNT() AS count", as_dict=True)[0].count or 0

        # Reset unread counter for the current reader at DB level
        frappe.db.sql(
            f"""
            UPDATE `tabAOS Conversation`
            SET {unread_field} = 0
            WHERE name = %s
            """,
            (conv_id,),
        )

        # Notify sender that their messages were read
        if updated_count > 0:
            frappe.publish_realtime(
                event="aos_message_status",
                message={
                    "conversation_id": conv_id,
                    "status": "read",
                    "read_at": now,
                },
                user=other_user,
            )

        publish_presence_update_to_peers(current_user)

        return ok(
            "Messages marked as read.",
            data={"updated_count": updated_count},
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Mark Read Failed",
        )
        frappe.db.rollback()
        return fail("Failed to update read status.", code="INTERNAL_ERROR")
