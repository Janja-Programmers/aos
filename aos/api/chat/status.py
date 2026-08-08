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
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.chat.events import publish_after_commit

from .constants import (
    MARK_DELIVERED_LIMIT_PER_MINUTE_PER_USER,
    MARK_READ_LIMIT_PER_MINUTE_PER_USER,
)

from .presence import schedule_presence_update_to_peers


# Helpers
def _get_conversation_row(conv_id: str):
    rows = frappe.db.sql(
        """SELECT name, participant_1, participant_2
        FROM `tabAOS Conversation` WHERE name = %s LIMIT 1 FOR UPDATE""",
        (conv_id,),
        as_dict=True,
    )
    return rows[0] if rows else None


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


MAX_STATUS_EVENT_IDS = 500


def _mark_incoming_status(
    *,
    conversation_id: str,
    sender: str,
    status: str,
    changed_at,
) -> tuple[int, List[str], bool]:
    """Update pending incoming rows in bounded batches under conversation lock."""
    updated = 0
    event_ids: List[str] = []
    batch_size = 500
    while True:
        if status == "delivered":
            ids = frappe.db.sql(
                """
                SELECT name FROM `tabAOS Message`
                WHERE conversation = %(conversation_id)s
                  AND sender = %(sender)s
                  AND delivered_to_receiver_at IS NULL
                ORDER BY creation ASC, name ASC
                LIMIT %(limit)s
                FOR UPDATE
                """,
                {"conversation_id": conversation_id, "sender": sender, "limit": batch_size},
                pluck=True,
            )
            if not ids:
                break
            frappe.db.sql(
                """
                UPDATE `tabAOS Message`
                SET delivered_to_receiver_at = %(changed_at)s
                WHERE name IN %(message_ids)s
                """,
                {"changed_at": changed_at, "message_ids": tuple(ids)},
            )
        else:
            ids = frappe.db.sql(
                """
                SELECT name FROM `tabAOS Message`
                WHERE conversation = %(conversation_id)s
                  AND sender = %(sender)s
                  AND read_by_receiver_at IS NULL
                ORDER BY creation ASC, name ASC
                LIMIT %(limit)s
                FOR UPDATE
                """,
                {"conversation_id": conversation_id, "sender": sender, "limit": batch_size},
                pluck=True,
            )
            if not ids:
                break
            frappe.db.sql(
                """
                UPDATE `tabAOS Message`
                SET read_by_receiver_at = %(changed_at)s,
                    delivered_to_receiver_at = COALESCE(delivered_to_receiver_at, %(changed_at)s)
                WHERE name IN %(message_ids)s
                """,
                {"changed_at": changed_at, "message_ids": tuple(ids)},
            )
        updated += len(ids)
        if len(event_ids) < MAX_STATUS_EVENT_IDS:
            event_ids.extend(ids[: MAX_STATUS_EVENT_IDS - len(event_ids)])
        if len(ids) < batch_size:
            break
    return updated, event_ids, updated > len(event_ids)

def _reset_unread_counter(
    *,
    conversation_id: str,
    unread_field: str,
) -> None:
    """
    Reset unread count for the current reader.

    unread_field is controlled internally, not user input.
    """

    if unread_field == "unread_count_1":
        frappe.db.sql(
            "UPDATE `tabAOS Conversation` SET unread_count_1 = 0 WHERE name = %s",
            (conversation_id,),
        )
    else:
        frappe.db.sql(
            "UPDATE `tabAOS Conversation` SET unread_count_2 = 0 WHERE name = %s",
            (conversation_id,),
        )


# mark_delivered
def mark_delivered_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "mark_delivered", current_user),
        ttl_seconds=60,
        limit=MARK_DELIVERED_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")

    if not conv_id:
        return fail("conversation_id is required.", error="VALIDATION_ERROR")

    try:
        conv = _get_conversation_row(conv_id)
        if not conv:
            return fail("Conversation not found.", error="NOT_FOUND")

        if not _validate_participant(conv, current_user):
            return fail("Not allowed.", error="PERMISSION_DENIED")

        other_user = _get_other_user(conv, current_user)
        if not other_user:
            return fail("Conversation participant not found.", error="NOT_FOUND")

        now = now_datetime()

        # Mark every pending incoming row with one bounded status event payload.
        updated_count, changed_message_ids, ids_truncated = _mark_incoming_status(
            conversation_id=conv_id,
            sender=other_user,
            status="delivered",
            changed_at=now,
        )

        if updated_count > 0:
            publish_after_commit(
                event="aos_message_status",
                message={
                    "conversation_id": conv_id,
                    "status": "delivered",
                    "receiver": public_account_id_for_user(current_user),
                    "message_ids": changed_message_ids,
                    "delivered_at": now,
                    "message_ids_truncated": ids_truncated,
                },
                user=other_user,
            )

        schedule_presence_update_to_peers(current_user)

        return ok(
            "Messages marked as delivered.",
            data={
                "updated_count": updated_count,
                "message_ids": changed_message_ids,
                "delivered_at": now if updated_count > 0 else None,
                "message_ids_truncated": ids_truncated,
            },
        )

    except Exception:
        frappe.log_error("Chat operation failed.", "AOS Mark Delivered Failed")
        return fail("Failed to update delivered status.", error="INTERNAL_ERROR")


# mark_read
def mark_read_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "mark_read", current_user),
        ttl_seconds=60,
        limit=MARK_READ_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")

    if not conv_id:
        return fail("conversation_id is required.", error="VALIDATION_ERROR")

    try:
        conv = _get_conversation_row(conv_id)
        if not conv:
            return fail("Conversation not found.", error="NOT_FOUND")

        if not _validate_participant(conv, current_user):
            return fail("Not allowed.", error="PERMISSION_DENIED")

        other_user = _get_other_user(conv, current_user)
        if not other_user:
            return fail("Conversation participant not found.", error="NOT_FOUND")

        unread_field = _get_unread_field_for_reader(conv, current_user)
        now = now_datetime()

        updated_count, changed_message_ids, ids_truncated = _mark_incoming_status(
            conversation_id=conv_id,
            sender=other_user,
            status="read",
            changed_at=now,
        )

        # Reset unread counter even if no message row changed.
        # This also repairs stale counters.
        _reset_unread_counter(
            conversation_id=conv_id,
            unread_field=unread_field,
        )

        if updated_count > 0:
            publish_after_commit(
                event="aos_message_status",
                message={
                    "conversation_id": conv_id,
                    "status": "read",
                    "reader": public_account_id_for_user(current_user),
                    "message_ids": changed_message_ids,
                    "read_at": now,
                    "message_ids_truncated": ids_truncated,
                },
                user=other_user,
            )

        schedule_presence_update_to_peers(current_user)

        return ok(
            "Messages marked as read.",
            data={
                "updated_count": updated_count,
                "message_ids": changed_message_ids,
                "read_at": now if updated_count > 0 else None,
                "message_ids_truncated": ids_truncated,
            },
        )

    except Exception:
        frappe.log_error("Chat operation failed.", "AOS Mark Read Failed")
        return fail("Failed to update read status.", error="INTERNAL_ERROR")
