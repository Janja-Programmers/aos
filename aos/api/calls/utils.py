"""
Call utilities.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime


def upsert_call_system_message(
    *,
    call_id: str,
    conversation_id: str,
    content: str,
):
    """
    Create or update a system message for a call.

    Guarantees:
        - Only ONE message per call (via call_id)
        - Updates message instead of inserting duplicates
        - Safe to call multiple times (idempotent)
    """
    # Check if message already exists for this call
    existing_message = frappe.db.get_value(
        "AOS Message",
        {
            "call_id": call_id,
            "message_type": "system",
        },
        ["name"],
    )

    if existing_message:
        # UPDATE existing message
        frappe.db.set_value(
            "AOS Message",
            existing_message,
            {"content": content},
            update_modified=False,
        )
        msg_name = existing_message

    else:
        # CREATE new message (first call event)
        msg = frappe.new_doc("AOS Message")
        msg.conversation = conversation_id
        msg.sender = "Administrator"
        msg.message_type = "system"
        msg.content = content
        msg.call_id = call_id
        msg.insert(ignore_permissions=True)

        msg_name = msg.name

    # Update conversation last message
    frappe.db.set_value(
        "AOS Conversation",
        conversation_id,
        {
            "last_message": content,
            "last_message_at": now_datetime(),
        },
        update_modified=False,
    )

    return msg_name
