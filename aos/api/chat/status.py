"""
Status APIs (implementation).

Handles:
- mark_delivered
- mark_read
"""

from __future__ import annotations
from typing import List

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
    """
    unread_count_1 belongs to participant_1.
    unread_count_2 belongs to participant_2.
    """

    return "unread_count_1" if conv.participant_1 == reader else "unread_count_2"


def _get_undelivered_incoming_message_ids(
    *,
    conversation_id: str,
    sender: str,
) -> List[str]:
    """
    Get messages sent by `sender` into this conversation that have not
    yet been marked delivered by the receiver.
    """

    return frappe.get_all(
        "AOS Message",
        filters={
            "conversation": conversation_id,
            "sender": sender,
            "delivered_to_receiver_at": ["is", "not set"],
        },
        pluck="name",
        order_by="creation asc",
    )


def _get_unread_incoming_message_ids(
    *,
    conversation_id: str,
    sender: str,
) -> List[str]:
    """
    Get messages sent by `sender` into this conversation that have not
    yet been marked read by the receiver.
    """

    return frappe.get_all(
        "AOS Message",
        filters={
            "conversation": conversation_id,
            "sender": sender,
            "read_by_receiver_at": ["is", "not set"],
        },
        pluck="name",
        order_by="creation asc",
    )


def _mark_messages_delivered(
    *,
    message_ids: List[str],
    delivered_at,
) -> int:
    if not message_ids:
        return 0

    frappe.db.sql(
        """
        UPDATE `tabAOS Message`
        SET delivered_to_receiver_at = %(delivered_at)s
        WHERE name IN %(message_ids)s
          AND delivered_to_receiver_at IS NULL
        """,
        {
            "message_ids": tuple(message_ids),
            "delivered_at": delivered_at,
        },
    )

    row = frappe.db.sql(
        "SELECT ROW_COUNT() AS count",
        as_dict=True,
    )

    return row[0].count or 0


def _mark_messages_read(
    *,
    message_ids: List[str],
    read_at,
) -> int:
    if not message_ids:
        return 0

    frappe.db.sql(
        """
        UPDATE `tabAOS Message`
        SET
            read_by_receiver_at = %(read_at)s,
            delivered_to_receiver_at = COALESCE(
                delivered_to_receiver_at,
                %(read_at)s
            )
        WHERE name IN %(message_ids)s
          AND read_by_receiver_at IS NULL
        """,
        {
            "message_ids": tuple(message_ids),
            "read_at": read_at,
        },
    )

    row = frappe.db.sql(
        "SELECT ROW_COUNT() AS count",
        as_dict=True,
    )

    return row[0].count or 0


def _reset_unread_counter(
    *,
    conversation_id: str,
    unread_field: str,
) -> None:
    """
    Reset unread count for the current reader.

    unread_field is controlled internally, not user input.
    """

    frappe.db.sql(
        f"""
        UPDATE `tabAOS Conversation`
        SET {unread_field} = 0
        WHERE name = %s
        """,
        (conversation_id,),
    )


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

        # Only messages sent by the other user can be marked as delivered
        # by the current user.
        message_ids = _get_undelivered_incoming_message_ids(
            conversation_id=conv_id,
            sender=other_user,
        )

        updated_count = _mark_messages_delivered(
            message_ids=message_ids,
            delivered_at=now,
        )

        changed_message_ids = message_ids if updated_count > 0 else []

        if updated_count > 0:
            frappe.publish_realtime(
                event="aos_message_status",
                message={
                    "conversation_id": conv_id,
                    "status": "delivered",
                    "receiver": current_user,
                    "message_ids": changed_message_ids,
                    "delivered_at": now,
                },
                user=other_user,
            )

        publish_presence_update_to_peers(current_user)

        return ok(
            "Messages marked as delivered.",
            data={
                "updated_count": updated_count,
                "message_ids": changed_message_ids,
                "delivered_at": now if updated_count > 0 else None,
            },
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

        # Only messages sent by the other user can be marked as read
        # by the current user.
        message_ids = _get_unread_incoming_message_ids(
            conversation_id=conv_id,
            sender=other_user,
        )

        updated_count = _mark_messages_read(
            message_ids=message_ids,
            read_at=now,
        )

        # Reset unread counter even if no message row changed.
        # This also repairs stale counters.
        _reset_unread_counter(
            conversation_id=conv_id,
            unread_field=unread_field,
        )

        changed_message_ids = message_ids if updated_count > 0 else []

        if updated_count > 0:
            frappe.publish_realtime(
                event="aos_message_status",
                message={
                    "conversation_id": conv_id,
                    "status": "read",
                    "reader": current_user,
                    "message_ids": changed_message_ids,
                    "read_at": now,
                },
                user=other_user,
            )

        publish_presence_update_to_peers(current_user)

        return ok(
            "Messages marked as read.",
            data={
                "updated_count": updated_count,
                "message_ids": changed_message_ids,
                "read_at": now if updated_count > 0 else None,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Mark Read Failed",
        )
        frappe.db.rollback()
        return fail("Failed to update read status.", code="INTERNAL_ERROR")
