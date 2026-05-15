"""Shared communication helpers.

These helpers are feature-neutral and can be reused by reviews, ads,
seller profile, notifications, or any feature that needs to know whether
two users have communicated.
"""

from __future__ import annotations

import frappe


def get_conversation_between_users(user_a: str | None, user_b: str | None) -> str | None:
    """
    Return the AOS Conversation between two users, if it exists.

    AOS Conversation stores participants in sorted order:
    - participant_1
    - participant_2
    """

    if not user_a or not user_b:
        return None

    if user_a == user_b:
        return None

    participant_1, participant_2 = sorted([user_a, user_b])

    return frappe.db.get_value(
        "AOS Conversation",
        {
            "participant_1": participant_1,
            "participant_2": participant_2,
        },
        "name",
    )


def has_communication_between_users(user_a: str | None, user_b: str | None) -> bool:
    """
    Return True if two users have communicated.

    In AOS:
    - normal chat creates AOS Message
    - call activity also creates/updates AOS Message with message_type='system'

    So checking AOS Message by conversation covers both chat and calls.
    """

    conversation = get_conversation_between_users(user_a, user_b)

    if not conversation:
        return False

    return bool(
        frappe.db.exists(
            "AOS Message",
            {
                "conversation": conversation,
            },
        )
    )
