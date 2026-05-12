"""
Call utilities.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

SYSTEM_MESSAGE_SENDER = "Administrator"


def _validate_system_message_inputs(
    *,
    call_id: str,
    conversation_id: str,
    content: str,
) -> None:
    if not call_id:
        frappe.throw("call_id is required for call system message")

    if not conversation_id:
        frappe.throw("conversation_id is required for call system message")

    if not (content or "").strip():
        frappe.throw("content is required for call system message")


def upsert_call_system_message(
    *,
    call_id: str,
    conversation_id: str,
    content: str,
):
    """
    Create or update a system message for a call.

    Guarantees:
    - Only one message per call via call_id.
    - Updates the existing message instead of inserting duplicates.
    - Safe to call multiple times.
    - Keeps conversation last_message in sync with the latest call state.

    Notes:
    - AOS Message.sender is required, so system messages use Administrator.
    - AOS Message validation allows message_type == "system" even when
      sender is not a conversation participant.
    """

    _validate_system_message_inputs(
        call_id=call_id,
        conversation_id=conversation_id,
        content=content,
    )

    content = content.strip()
    now = now_datetime()

    existing_message = frappe.db.get_value(
        "AOS Message",
        {
            "call_id": call_id,
            "message_type": "system",
        },
        "name",
    )

    if existing_message:
        frappe.db.set_value(
            "AOS Message",
            existing_message,
            {
                "content": content,
            },
            update_modified=False,
        )

        msg_name = existing_message

    else:
        msg = frappe.new_doc("AOS Message")
        msg.conversation = conversation_id
        msg.sender = SYSTEM_MESSAGE_SENDER
        msg.message_type = "system"
        msg.content = content
        msg.call_id = call_id
        msg.insert(ignore_permissions=True)

        msg_name = msg.name

    frappe.db.set_value(
        "AOS Conversation",
        conversation_id,
        {
            "last_message": content,
            "last_message_at": now,
            "last_sender": SYSTEM_MESSAGE_SENDER,
        },
        update_modified=False,
    )

    return msg_name
