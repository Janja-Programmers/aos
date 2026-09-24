"""Durable Chat-owned system events for group conversation history.

These are conversation events, not user-authored messages. They make group
membership/metadata changes reconstructable after missed realtime events and
across devices. The database remains authoritative; realtime only accelerates
refresh.
"""
from __future__ import annotations

from collections.abc import Iterable

import frappe
from frappe.utils import now_datetime

from aos.api.shared.user_display import get_user_display_map
from aos.services.chat.events import publish_after_commit
from aos.services.chat.membership import active_members

SYSTEM_MESSAGE_SENDER = "Administrator"


def _display_names(users: Iterable[str]) -> dict[str, str]:
    ordered = list(dict.fromkeys(str(user) for user in users if user))
    if not ordered:
        return {}
    displays = get_user_display_map(ordered)
    return {
        user: str((displays.get(user) or {}).get("display_name") or "AOS User")
        for user in ordered
    }


def display_name(user: str) -> str:
    return _display_names([user]).get(str(user), "AOS User")


def display_names(users: Iterable[str]) -> list[str]:
    ordered = list(dict.fromkeys(str(user) for user in users if user))
    mapping = _display_names(ordered)
    return [mapping.get(user, "AOS User") for user in ordered]


def format_display_names(users: Iterable[str]) -> str:
    names = display_names(users)
    if not names:
        return "AOS User"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def group_system_message(*, conversation_id: str, actor: str, content: str) -> str:
    """Insert one durable group event and project it into every active member inbox.

    The actor sees the event but it does not increment their unread count. Other
    active members get one unread item. Removed/left users are not recipients.
    """
    clean = str(content or "").strip()
    if not clean:
        frappe.throw("System message content is required")

    members = active_members(conversation_id)
    recipients = [str(row.user) for row in members if row.user]
    if not recipients:
        frappe.throw("Group has no active participants")

    msg = frappe.new_doc("AOS Message")
    msg.conversation = conversation_id
    msg.sender = SYSTEM_MESSAGE_SENDER
    msg.message_type = "system"
    msg.content = clean
    msg.recipient_count = max(0, len(recipients) - (1 if actor in recipients else 0))
    msg.insert(ignore_permissions=True)

    at = msg.creation or now_datetime()
    frappe.db.set_value(
        "AOS Conversation",
        conversation_id,
        {"last_message": clean, "last_message_at": at, "last_sender": SYSTEM_MESSAGE_SENDER},
        update_modified=False,
    )
    frappe.db.sql(
        """
        UPDATE `tabAOS Conversation Participant`
        SET is_hidden=0,
            last_visible_message=%(message)s,
            last_visible_message_at=%(at)s,
            last_visible_sender=%(sender)s,
            unread_count=CASE
                WHEN user=%(actor)s THEN COALESCE(unread_count,0)
                ELSE COALESCE(unread_count,0)+1
            END
        WHERE conversation=%(conversation)s AND status='active'
        """,
        {
            "message": msg.name,
            "at": at,
            "sender": SYSTEM_MESSAGE_SENDER,
            "actor": actor,
            "conversation": conversation_id,
        },
    )

    for recipient in recipients:
        publish_after_commit(
            event="aos_new_message",
            message={"conversation_id": conversation_id, "message_id": msg.name},
            user=recipient,
        )
    return str(msg.name)
