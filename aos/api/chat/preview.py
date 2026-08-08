"""
Chat conversation preview helpers.

These helpers maintain participant-specific conversation previews.

Why participant-specific previews exist:
- If participant 1 deletes a message "for me", participant 1 should no longer
  see that message as the conversation preview.
- Participant 2 should still see it.
- If a sender deletes a message for everyone, sender should see
  "You deleted this message", while receiver should see
  "This message was deleted".
- Therefore AOS Conversation stores:
    last_message_1 / last_message_at_1 / last_sender_1
    last_message_2 / last_message_at_2 / last_sender_2
"""

from __future__ import annotations

from typing import Any, Dict

import frappe

from aos.services.chat.shared_objects import fetch_chat_ad_previews

from .visibility import (
    get_deleted_for_everyone_display_text,
    get_last_message_at_field,
    get_last_message_field,
    get_last_sender_field,
    is_deleted_for_everyone,
)


def _fetch_ads_bulk(
    ad_ids: list[str],
    *,
    viewer: str | None = None,
) -> Dict[str, Dict[str, Any]]:
    return fetch_chat_ad_previews(ad_ids, viewer=viewer)

def build_message_preview(
    msg,
    *,
    viewer: str,
    ad_map: Dict[str, Dict[str, Any]] | None = None,
) -> str:
    """
    Build a display-safe conversation preview for one viewer.

    Delete-for-everyone previews are viewer-specific:
    - sender sees: You deleted this message
    - receiver sees: This message was deleted
    """

    if is_deleted_for_everyone(msg):
        return get_deleted_for_everyone_display_text(
            sender=getattr(msg, "sender", None),
            viewer=viewer,
        )

    content = (getattr(msg, "content", None) or "").strip()
    if content:
        return content

    ad = getattr(msg, "ad", None)
    if ad:
        ad_preview = (ad_map or {}).get(ad)
        title = ad_preview.get("title") if ad_preview else None
        return title or "[Ad]"

    if getattr(msg, "short", None):
        return "[Short]"

    if getattr(msg, "live", None):
        return "[Live]"

    if bool(getattr(msg, "has_attachments", 0)):
        return "[Attachment]"

    return "[Message]"


def _get_latest_visible_message_for_user(
    *,
    conversation_id: str,
    conv,
    user: str,
):
    """
    Fetch latest message visible to this user.

    Delete-for-me messages are excluded.
    Delete-for-everyone messages are included because they render as placeholders.
    """

    if user == conv.participant_1:
        participant_index = 1
    elif user == conv.participant_2:
        participant_index = 2
    else:
        return None

    rows = frappe.db.sql(
        """
        SELECT
            name, conversation, sender, content, message_type, ad, short, live,
            reply_to_message, has_attachments,
            is_forwarded, forwarded_from_message, forwarded_from_conversation,
            is_edited, edited_at,
            deleted_for_everyone, deleted_for_everyone_at,
            deleted_for_1, deleted_for_1_at, deleted_for_2, deleted_for_2_at,
            delivered_to_receiver_at, read_by_receiver_at, creation
        FROM `tabAOS Message`
        WHERE conversation = %(conversation_id)s
          AND (
                (%(participant_index)s = 1 AND IFNULL(deleted_for_1, 0) = 0)
             OR (%(participant_index)s = 2 AND IFNULL(deleted_for_2, 0) = 0)
          )
        ORDER BY creation DESC, name DESC
        LIMIT 1
        """,
        {"conversation_id": conversation_id, "participant_index": participant_index},
        as_dict=True,
    )

    if not rows:
        return None

    return rows[0]


def recompute_conversation_preview_for_user(
    *,
    conversation_id: str,
    user: str,
) -> None:
    """
    Recompute participant-specific preview for one user.
    """

    conv = frappe.db.get_value(
        "AOS Conversation",
        conversation_id,
        ["name", "participant_1", "participant_2"],
        as_dict=True,
    )

    if not conv:
        return

    if user not in (conv.participant_1, conv.participant_2):
        return

    last_message_field = get_last_message_field(conv, user)
    last_message_at_field = get_last_message_at_field(conv, user)
    last_sender_field = get_last_sender_field(conv, user)

    latest = _get_latest_visible_message_for_user(
        conversation_id=conversation_id,
        conv=conv,
        user=user,
    )

    if not latest:
        frappe.db.set_value(
            "AOS Conversation",
            conversation_id,
            {
                last_message_field: None,
                last_message_at_field: None,
                last_sender_field: None,
            },
            update_modified=False,
        )
        return

    ad_map = _fetch_ads_bulk([latest.ad], viewer=user) if latest.ad else {}

    preview = build_message_preview(
        latest,
        viewer=user,
        ad_map=ad_map,
    )

    frappe.db.set_value(
        "AOS Conversation",
        conversation_id,
        {
            last_message_field: preview,
            last_message_at_field: latest.creation,
            last_sender_field: latest.sender,
        },
        update_modified=False,
    )


def recompute_conversation_previews(conversation_id: str) -> None:
    """
    Recompute participant-specific previews for both users.
    """

    conv = frappe.db.get_value(
        "AOS Conversation",
        conversation_id,
        ["name", "participant_1", "participant_2"],
        as_dict=True,
    )

    if not conv:
        return

    recompute_conversation_preview_for_user(
        conversation_id=conversation_id,
        user=conv.participant_1,
    )

    recompute_conversation_preview_for_user(
        conversation_id=conversation_id,
        user=conv.participant_2,
    )


def set_conversation_preview_for_new_message(
    *,
    conversation_id: str,
    sender: str,
    preview: str,
    sent_at,
) -> None:
    """
    Fast path for send_message / forward_message.

    A newly sent message is visible to both participants, so both previews can be
    updated directly without scanning the message table.

    A newly sent/forwarded message should also reactivate both participants'
    conversation list visibility. Deleting a conversation only hides it until
    a new message arrives or the user opens it again.
    """

    frappe.db.set_value(
        "AOS Conversation",
        conversation_id,
        {
            "last_message_1": preview,
            "last_message_at_1": sent_at,
            "last_sender_1": sender,
            "last_message_2": preview,
            "last_message_at_2": sent_at,
            "last_sender_2": sender,
            "is_active_1": 1,
            "is_active_2": 1,
        },
        update_modified=False,
    )


def set_conversation_preview_for_deleted_everyone(
    *,
    conversation_id: str,
    sender: str,
    deleted_at,
    participant_1: str,
    participant_2: str,
) -> None:
    """
    Set participant-specific previews for a latest message deleted for everyone.

    Use this only when the deleted message should remain the latest preview.
    If delete-for-me visibility differs per participant, prefer
    recompute_conversation_previews().
    """

    frappe.db.set_value(
        "AOS Conversation",
        conversation_id,
        {
            "last_message_1": get_deleted_for_everyone_display_text(
                sender=sender,
                viewer=participant_1,
            ),
            "last_message_at_1": deleted_at,
            "last_sender_1": sender,
            "last_message_2": get_deleted_for_everyone_display_text(
                sender=sender,
                viewer=participant_2,
            ),
            "last_message_at_2": deleted_at,
            "last_sender_2": sender,
        },
        update_modified=False,
    )

