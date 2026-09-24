"""
Call utilities.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.services.calls.identifiers import PUBLIC_CALL_ID_RE

SYSTEM_MESSAGE_SENDER = "Administrator"


def _validate_system_message_inputs(
    *,
    call_id: str,
    conversation_id: str,
    content: str,
) -> None:
    if not PUBLIC_CALL_ID_RE.fullmatch(str(call_id or "").strip()):
        frappe.throw("A canonical public call_id is required for call system message")
    if not frappe.db.exists("AOS Call", {"public_id": call_id}):
        frappe.throw("Call not found")

    if not conversation_id:
        frappe.throw("conversation_id is required for call system message")

    if not (content or "").strip():
        frappe.throw("content is required for call system message")


def _validate_conversation_exists(conversation_id: str) -> None:
    rows = frappe.db.sql(
        "SELECT name FROM `tabAOS Conversation` WHERE name=%s AND conversation_type='direct' LIMIT 1 FOR UPDATE",
        (conversation_id,),
    )
    if not rows:
        frappe.throw("Direct conversation not found")


def _set_conversation_call_preview(
    *, conversation_id: str, message_id: str, content: str, sender: str, timestamp
) -> None:
    """Project the Calls-owned durable log into the direct Chat inbox."""
    frappe.db.set_value(
        "AOS Conversation", conversation_id,
        {"last_message": content, "last_message_at": timestamp, "last_sender": sender},
        update_modified=False,
    )
    frappe.db.sql(
        """UPDATE `tabAOS Conversation Participant`
           SET is_hidden=0, last_visible_message=%(message)s, last_visible_message_at=%(at)s,
               last_visible_sender=%(sender)s
           WHERE conversation=%(conversation)s AND status='active'""",
        {"message": message_id, "at": timestamp, "sender": sender, "conversation": conversation_id},
    )


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
    - Keeps participant-specific conversation previews in sync with the latest
      call state.

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

    _validate_conversation_exists(conversation_id)

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
        msg.recipient_count = 0
        try:
            msg.insert(ignore_permissions=True)
            msg_name = msg.name
        except frappe.DuplicateEntryError:
            # The database uniqueness rule on call_id elects one concurrent
            # writer. Reuse that durable winner instead of creating a duplicate.
            msg_name = frappe.db.get_value(
                "AOS Message",
                {"call_id": call_id, "message_type": "system"},
                "name",
            )
            if not msg_name:
                raise
            frappe.db.set_value(
                "AOS Message",
                msg_name,
                {"content": content},
                update_modified=False,
            )

    _set_conversation_call_preview(
        conversation_id=conversation_id, message_id=msg_name, content=content,
        sender=SYSTEM_MESSAGE_SENDER, timestamp=now,
    )

    return msg_name
