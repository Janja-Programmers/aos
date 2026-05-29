"""
Message reaction APIs (implementation).

Handles:
- toggle_message_reaction

Behavior:
- Add a reaction if none exists.
- Change reaction if a different emoji exists.
- Remove reaction if the same emoji is sent again.
- Remove reaction if emoji is empty/null.
- Reactions are visible to both participants.
- Reactions do not affect conversation preview or unread counts.
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import TOGGLE_MESSAGE_REACTION_LIMIT_PER_MINUTE_PER_USER
from .visibility import get_deleted_for_user_field, get_other_participant


MAX_EMOJI_LENGTH = 16


def _clean_emoji(value) -> str | None:
    """
    Normalize emoji input.

    Empty string / None means remove the current user's reaction.
    """

    if value is None:
        return None

    emoji = str(value).strip()

    if not emoji:
        return None

    return emoji


def _get_message_with_conversation(message_id: str):
    """
    Fetch message together with conversation participants.
    """

    rows = frappe.db.sql(
        """
        SELECT
            m.name,
            m.conversation,
            m.sender,
            m.message_type,
            m.deleted_for_everyone,
            m.deleted_for_1,
            m.deleted_for_2,

            c.participant_1,
            c.participant_2
        FROM `tabAOS Message` m
        INNER JOIN `tabAOS Conversation` c
            ON c.name = m.conversation
        WHERE m.name = %(message_id)s
        LIMIT 1
        """,
        {"message_id": message_id},
        as_dict=True,
    )

    return rows[0] if rows else None


def _get_existing_reaction(*, message_id: str, user: str):
    """
    Return existing reaction row for this user/message, if any.
    """

    return frappe.db.get_value(
        "AOS Message Reaction",
        {
            "message": message_id,
            "user": user,
        },
        ["name", "emoji"],
        as_dict=True,
    )


def _validate_message_can_be_reacted_to(msg, current_user: str):
    if current_user not in (msg.participant_1, msg.participant_2):
        return fail("Not allowed.", code="PERMISSION_DENIED")

    if bool(msg.deleted_for_everyone):
        return fail(
            "Deleted messages cannot be reacted to.",
            code="VALIDATION_ERROR",
        )

    delete_field = get_deleted_for_user_field(msg, current_user)

    if bool(getattr(msg, delete_field, 0)):
        return fail(
            "You cannot react to a message deleted for you.",
            code="VALIDATION_ERROR",
        )

    if msg.message_type == "system":
        return fail(
            "System messages cannot be reacted to.",
            code="VALIDATION_ERROR",
        )

    return None


def _validate_emoji(emoji: str | None):
    if emoji is None:
        return None

    if len(emoji) > MAX_EMOJI_LENGTH:
        return fail(
            f"Emoji cannot exceed {MAX_EMOJI_LENGTH} characters.",
            code="VALIDATION_ERROR",
        )

    return None


def fetch_message_reaction_summary(
    *,
    message_id: str,
    viewer: str,
) -> List[Dict[str, Any]]:
    """
    Return reaction summary for one message, viewer-aware.

    Shape:
        [
            {
                "emoji": "😂",
                "count": 2,
                "reacted_by_me": true
            }
        ]
    """

    if not message_id:
        return []

    rows = frappe.db.sql(
        """
        SELECT
            emoji,
            COUNT(*) AS reaction_count,
            MAX(CASE WHEN user = %(viewer)s THEN 1 ELSE 0 END) AS reacted_by_me
        FROM `tabAOS Message Reaction`
        WHERE message = %(message_id)s
        GROUP BY emoji
        ORDER BY reaction_count DESC, emoji ASC
        """,
        {
            "message_id": message_id,
            "viewer": viewer,
        },
        as_dict=True,
    )

    return [
        {
            "emoji": row.emoji,
            "count": int(row.reaction_count or 0),
            "reacted_by_me": bool(row.reacted_by_me),
        }
        for row in rows
    ]


def fetch_message_reaction_summaries(
    *,
    message_ids: List[str],
    viewer: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Bulk reaction summary fetcher for list_messages/list_starred_messages.

    Returns:
        {
            "MSG-001": [
                {"emoji": "😂", "count": 2, "reacted_by_me": True}
            ]
        }
    """

    if not message_ids:
        return {}

    unique_ids = list({message_id for message_id in message_ids if message_id})

    if not unique_ids:
        return {}

    rows = frappe.db.sql(
        """
        SELECT
            message,
            emoji,
            COUNT(*) AS reaction_count,
            MAX(CASE WHEN user = %(viewer)s THEN 1 ELSE 0 END) AS reacted_by_me
        FROM `tabAOS Message Reaction`
        WHERE message IN %(message_ids)s
        GROUP BY message, emoji
        ORDER BY message ASC, reaction_count DESC, emoji ASC
        """,
        {
            "message_ids": tuple(unique_ids),
            "viewer": viewer,
        },
        as_dict=True,
    )

    grouped: Dict[str, List[Dict[str, Any]]] = {}

    for row in rows:
        grouped.setdefault(row.message, []).append(
            {
                "emoji": row.emoji,
                "count": int(row.reaction_count or 0),
                "reacted_by_me": bool(row.reacted_by_me),
            }
        )

    return grouped


def fetch_my_reactions(
    *,
    message_ids: List[str],
    user: str,
) -> Dict[str, str]:
    """
    Fetch current user's reaction per message.

    Returns:
        {
            "MSG-001": "😂"
        }
    """

    if not message_ids or not user:
        return {}

    unique_ids = list({message_id for message_id in message_ids if message_id})

    if not unique_ids:
        return {}

    rows = frappe.get_all(
        "AOS Message Reaction",
        filters={
            "message": ["in", unique_ids],
            "user": user,
        },
        fields=["message", "emoji"],
    )

    return {row.message: row.emoji for row in rows}


def _publish_reaction_update(
    *,
    msg,
    current_user: str,
) -> None:
    """
    Notify the other participant that reactions changed.

    The payload is viewer-aware for the receiver.
    """

    receiver = get_other_participant(msg, current_user)

    receiver_reactions = fetch_message_reaction_summary(
        message_id=msg.name,
        viewer=receiver,
    )

    receiver_my_reaction = frappe.db.get_value(
        "AOS Message Reaction",
        {
            "message": msg.name,
            "user": receiver,
        },
        "emoji",
    )

    frappe.publish_realtime(
        event="aos_message_reaction_updated",
        message={
            "conversation_id": msg.conversation,
            "message_id": msg.name,
            "reactions": receiver_reactions,
            "viewer_state": {
                "my_reaction": receiver_my_reaction,
            },
        },
        user=receiver,
    )


def toggle_message_reaction_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:reaction:toggle:user:{current_user}",
        ttl_seconds=60,
        limit=TOGGLE_MESSAGE_REACTION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many reaction requests. Please slow down.",
    )
    if rl:
        return rl

    message_id = kwargs.get("message_id")
    emoji = _clean_emoji(kwargs.get("emoji"))

    if not message_id:
        return fail("message_id is required.", code="VALIDATION_ERROR")

    emoji_error = _validate_emoji(emoji)
    if emoji_error:
        return emoji_error

    try:
        msg = _get_message_with_conversation(message_id)

        if not msg:
            return fail("Message not found.", code="NOT_FOUND")

        validation_error = _validate_message_can_be_reacted_to(
            msg,
            current_user,
        )
        if validation_error:
            return validation_error

        existing = _get_existing_reaction(
            message_id=message_id,
            user=current_user,
        )

        action = "none"

        # No emoji means remove reaction if it exists.
        if emoji is None:
            if existing:
                frappe.delete_doc(
                    "AOS Message Reaction",
                    existing.name,
                    ignore_permissions=True,
                )
                action = "removed"
            else:
                action = "none"

        elif existing and existing.emoji == emoji:
            # Tapping the same reaction removes it.
            frappe.delete_doc(
                "AOS Message Reaction",
                existing.name,
                ignore_permissions=True,
            )
            action = "removed"

        elif existing:
            # Change existing reaction.
            frappe.db.set_value(
                "AOS Message Reaction",
                existing.name,
                {
                    "emoji": emoji,
                },
                update_modified=True,
            )
            action = "updated"

        else:
            # Create new reaction.
            reaction = frappe.new_doc("AOS Message Reaction")
            reaction.message = message_id
            reaction.conversation = msg.conversation
            reaction.user = current_user
            reaction.emoji = emoji
            reaction.insert(ignore_permissions=True)
            action = "added"

        reactions = fetch_message_reaction_summary(
            message_id=message_id,
            viewer=current_user,
        )

        my_reaction = frappe.db.get_value(
            "AOS Message Reaction",
            {
                "message": message_id,
                "user": current_user,
            },
            "emoji",
        )

        _publish_reaction_update(
            msg=msg,
            current_user=current_user,
        )

        return ok(
            "Message reaction updated.",
            data={
                "conversation_id": msg.conversation,
                "message_id": message_id,
                "action": action,
                "emoji": my_reaction,
                "reactions": reactions,
                "viewer_state": {
                    "my_reaction": my_reaction,
                },
            },
        )

    except frappe.DuplicateEntryError:
        frappe.db.rollback()
        return fail(
            "Reaction already exists. Please retry.",
            code="CONFLICT",
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Toggle Message Reaction Failed",
        )
        frappe.db.rollback()
        return fail("Failed to update message reaction.", code="INTERNAL_ERROR")
