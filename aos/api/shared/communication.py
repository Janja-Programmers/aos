"""Feature-neutral communication helpers backed by canonical direct Chat membership."""
from __future__ import annotations

import frappe
from aos.services.chat.membership import direct_key


def get_conversation_between_users(user_a: str | None, user_b: str | None) -> str | None:
    if not user_a or not user_b or user_a == user_b:
        return None
    return frappe.db.get_value("AOS Conversation", {"conversation_type":"direct", "direct_key":direct_key(user_a,user_b)}, "name")


def has_communication_between_users(user_a: str | None, user_b: str | None) -> bool:
    conversation=get_conversation_between_users(user_a,user_b)
    return bool(conversation and frappe.db.exists("AOS Message", {"conversation":conversation}))
