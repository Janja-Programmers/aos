"""
Message APIs (implementation).

Handles:
- send_message
- list_messages
"""

from __future__ import annotations

from typing import Any, List, Dict

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from aos.services.notification_service import NotificationService

from .constants import (
    SEND_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
    LIST_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
)

from .presence import publish_presence_update_to_peers


# Helpers
def _get_conversation_row(conv_id: str):
    return frappe.db.get_value(
        "AOS Conversation",
        conv_id,
        ["participant_1", "participant_2"],
        as_dict=True,
    )


def _validate_sender(conv, sender: str):
    return sender in (conv.participant_1, conv.participant_2)


def _get_receiver(conv, sender: str):
    return (
        conv.participant_2
        if conv.participant_1 == sender
        else conv.participant_1
    )


def _serialize_attachments_bulk(message_ids: List[str]) -> Dict[str, List[Dict]]:
    if not message_ids:
        return {}

    rows = frappe.get_all(
        "AOS Message Attachment",
        filters={"message": ["in", message_ids]},
        fields=["message", "file", "file_type", "sort_order"],
        order_by="sort_order asc",
    )

    if not rows:
        return {}

    file_ids = [r.file for r in rows if r.file]

    files = frappe.get_all(
        "File",
        filters={"name": ["in", file_ids]},
        fields=["name", "file_url"],
    )

    file_map = {f.name: f.file_url for f in files}

    grouped = {}

    for r in rows:
        if r.file not in file_map:
            continue

        grouped.setdefault(r.message, []).append(
            {
                "url": file_map[r.file],
                "type": r.file_type,
                "sort_order": r.sort_order,
            }
        )

    return grouped


# send_message
def send_message_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:send:user:{current_user}",
        ttl_seconds=60,
        limit=SEND_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many messages. Please slow down.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")
    content = (kwargs.get("content") or "").strip()
    ad = kwargs.get("ad")
    attachments = kwargs.get("attachments") or []

    if not conv_id:
        return fail("conversation_id is required.", code="VALIDATION_ERROR")

    try:
        conv = _get_conversation_row(conv_id)
        if not conv:
            return fail("Conversation not found.", code="NOT_FOUND")

        if not _validate_sender(conv, current_user):
            return fail("Not allowed.", code="PERMISSION_DENIED")

        if not content and not attachments:
            return fail(
                "Message must have content or attachments.",
                code="VALIDATION_ERROR",
            )

        receiver = _get_receiver(conv, current_user)

        # Determine message type
        if content and attachments:
            message_type = "mixed"
        elif attachments:
            message_type = "media"
        else:
            message_type = "text"

        now = now_datetime()

        # Create message
        msg = frappe.new_doc("AOS Message")
        msg.conversation = conv_id
        msg.sender = current_user
        msg.message_type = message_type
        msg.content = content or None

        if ad:
            msg.ad = ad

        msg.insert(ignore_permissions=True)

        # Attachments
        has_attachments = 0

        for i, att in enumerate(attachments):
            file_id = att.get("file")
            file_type = att.get("file_type")

            if not file_id or not file_type:
                frappe.db.rollback()
                return fail("Invalid attachment payload.", code="VALIDATION_ERROR")

            if not frappe.db.exists("File", file_id):
                frappe.db.rollback()
                return fail("Invalid file reference.", code="VALIDATION_ERROR")

            frappe.get_doc({
                "doctype": "AOS Message Attachment",
                "message": msg.name,
                "file": file_id,
                "file_type": file_type,
                "sort_order": i,
            }).insert(ignore_permissions=True)

            has_attachments = 1

        if has_attachments:
            msg.db_set("has_attachments", 1, update_modified=False)

        # Update conversation
        if current_user == conv.participant_1:
            unread_field = "unread_count_2"
        else:
            unread_field = "unread_count_1"

        frappe.db.sql(
            f"""
            UPDATE `tabAOS Conversation`
            SET
                last_message = %s,
                last_message_at = %s,
                last_sender = %s,
                {unread_field} = COALESCE({unread_field}, 0) + 1
            WHERE name = %s
            """,
            (
                content or "[Attachment]",
                now,
                current_user,
                conv_id,
            ),
        )

        # Realtime
        realtime_payload = {
            "conversation_id": conv_id,
            "message": {
                "id": msg.name,
                "sender": current_user,
                "content": msg.content,
                "message_type": msg.message_type,
                "ad": msg.ad,
                "has_attachments": has_attachments,
                "attachments": _serialize_attachments_bulk([msg.name]).get(msg.name, []),
                "created_at": msg.creation,
            },
        }

        frappe.publish_realtime(
            event="aos_new_message",
            message=realtime_payload,
            user=receiver,
        )

        # Notification
        NotificationService.notify_new_message(
            user=receiver,
            sender=current_user,
            conversation_id=conv_id,
            preview=msg.content or "[Attachment]",
        )

        # Presence trigger
        publish_presence_update_to_peers(current_user)

        return ok("Message sent.", data={"id": msg.name})

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Send Message Failed",
        )
        frappe.db.rollback()
        return fail("Failed to send message.", code="INTERNAL_ERROR")


# list_messages
def list_messages_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:list_msgs:user:{current_user}",
        ttl_seconds=60,
        limit=LIST_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")
    limit = int(kwargs.get("limit") or 30)
    before = kwargs.get("before")

    if not conv_id:
        return fail("conversation_id is required.", code="VALIDATION_ERROR")

    try:
        conv = _get_conversation_row(conv_id)
        if not conv:
            return fail("Conversation not found.", code="NOT_FOUND")

        if current_user not in (conv.participant_1, conv.participant_2):
            return fail("Not allowed.", code="PERMISSION_DENIED")

        filters = {"conversation": conv_id}

        if before:
            before_creation = frappe.db.get_value(
                "AOS Message",
                before,
                "creation",
            )

            if not before_creation:
                return fail("Invalid 'before' message.", code="VALIDATION_ERROR")

            filters["creation"] = ("<", before_creation)

        messages = frappe.get_all(
            "AOS Message",
            filters=filters,
            fields=[
                "name",
                "sender",
                "content",
                "message_type",
                "ad",
                "has_attachments",
                "delivered_to_receiver_at",
                "read_by_receiver_at",
                "creation",
            ],
            order_by="creation desc",
            limit_page_length=limit,
        )

        message_ids = [m.name for m in messages]

        attachments_map = _serialize_attachments_bulk(message_ids)

        results = []

        for m in messages:
            results.append(
                {
                    "id": m.name,
                    "sender": m.sender,
                    "content": m.content,
                    "message_type": m.message_type,
                    "ad": m.ad,
                    "has_attachments": m.has_attachments,
                    "attachments": attachments_map.get(m.name, []),
                    "delivered_at": m.delivered_to_receiver_at,
                    "read_at": m.read_by_receiver_at,
                    "created_at": m.creation,
                }
            )

        return ok("Messages fetched.", data=results)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Messages Failed",
        )
        return fail("Failed to fetch messages.", code="INTERNAL_ERROR")
