"""
Call utilities.
"""

from __future__ import annotations

import frappe


def insert_call_system_message(
    *,
    conversation_id: str,
    content: str,
):
    """
    Insert a system message for call events.
    """
    msg = frappe.new_doc("AOS Message")
    msg.conversation = conversation_id
    msg.sender = "Administrator"
    msg.message_type = "system"
    msg.content = content

    msg.insert(ignore_permissions=True)

    # Update conversation last message
    frappe.db.set_value(
        "AOS Conversation",
        conversation_id,
        {
            "last_message": content,
            "last_message_at": msg.creation,
        },
        update_modified=False,
    )

    return msg.name
