"""
Chat message visibility helpers.

These helpers centralize WhatsApp-like delete behavior:

- Delete for me:
    Message is hidden only for the current participant.

- Delete for everyone:
    Message remains visible as a deleted placeholder for both participants.

AOS Conversation is always 1-to-1, so participant-specific delete flags live
directly on AOS Message:
    deleted_for_1 / deleted_for_1_at
    deleted_for_2 / deleted_for_2_at
"""

from __future__ import annotations

from typing import Any

import frappe


DELETED_MESSAGE_TEXT = "This message was deleted"
YOU_DELETED_MESSAGE_TEXT = "You deleted this message"


def get_deleted_for_everyone_display_text(
    *,
    sender: str | None,
    viewer: str | None,
) -> str:
    """
    Return viewer-specific placeholder text for delete-for-everyone messages.

    Sender sees:
        You deleted this message

    Receiver sees:
        This message was deleted

    The DB stores only the neutral delete state:
        deleted_for_everyone = 1

    The API computes display text per viewer.
    """

    if sender and viewer and sender == viewer:
        return YOU_DELETED_MESSAGE_TEXT

    return DELETED_MESSAGE_TEXT


def get_participant_index(conv: Any, user: str) -> int | None:
    """
    Return the participant index for a user in a conversation.

    Returns:
        1 if user is participant_1
        2 if user is participant_2
        None if user is not part of the conversation
    """

    if not conv or not user:
        return None

    if user == getattr(conv, "participant_1", None):
        return 1

    if user == getattr(conv, "participant_2", None):
        return 2

    return None


def require_participant_index(conv: Any, user: str) -> int:
    """
    Return participant index or raise a Frappe validation error.

    Use this in backend internals after permission checks.
    """

    participant_index = get_participant_index(conv, user)

    if participant_index not in (1, 2):
        frappe.throw("User is not a participant in this conversation")

    return participant_index


def get_deleted_for_user_field(conv: Any, user: str) -> str:
    """
    Return the participant-specific delete flag field for this user.
    """

    participant_index = require_participant_index(conv, user)
    return f"deleted_for_{participant_index}"


def get_deleted_for_user_at_field(conv: Any, user: str) -> str:
    """
    Return the participant-specific delete timestamp field for this user.
    """

    participant_index = require_participant_index(conv, user)
    return f"deleted_for_{participant_index}_at"


def get_last_message_field(conv: Any, user: str) -> str:
    """
    Return the participant-specific last message field for this user.
    """

    participant_index = require_participant_index(conv, user)
    return f"last_message_{participant_index}"


def get_last_message_at_field(conv: Any, user: str) -> str:
    """
    Return the participant-specific last message timestamp field for this user.
    """

    participant_index = require_participant_index(conv, user)
    return f"last_message_at_{participant_index}"


def get_last_sender_field(conv: Any, user: str) -> str:
    """
    Return the participant-specific last sender field for this user.
    """

    participant_index = require_participant_index(conv, user)
    return f"last_sender_{participant_index}"


def is_deleted_for_everyone(msg: Any) -> bool:
    """
    Whether this message was deleted globally for both participants.
    """

    return bool(getattr(msg, "deleted_for_everyone", 0))


def is_deleted_for_user(msg: Any, conv: Any, user: str) -> bool:
    """
    Whether this message was deleted only for the given viewer.

    Note:
        deleted_for_everyone messages are still visible as placeholders,
        so this only checks participant-specific hide flags.
    """

    fieldname = get_deleted_for_user_field(conv, user)
    return bool(getattr(msg, fieldname, 0))


def is_visible_to_user(msg: Any, conv: Any, user: str) -> bool:
    """
    Whether a message should be returned in list_messages for this user.

    Rules:
        - deleted_for_user = hidden
        - deleted_for_everyone = visible as placeholder
    """

    if is_deleted_for_user(msg, conv, user):
        return False

    return True


def should_render_deleted_placeholder(msg: Any) -> bool:
    """
    Whether frontend should render the deleted-message placeholder.
    """

    return is_deleted_for_everyone(msg)


def get_other_participant(conv: Any, user: str) -> str:
    """
    Return the other participant in a 1-to-1 conversation.
    """

    participant_index = require_participant_index(conv, user)

    if participant_index == 1:
        return conv.participant_2

    return conv.participant_1


def get_user_delete_sql_condition(conv: Any, user: str) -> str:
    """
    Return SQL condition used to exclude messages deleted for the viewer.

    Example:
        IFNULL(deleted_for_1, 0) = 0
    """

    participant_index = require_participant_index(conv, user)
    return f"IFNULL(deleted_for_{participant_index}, 0) = 0"
