"""
Chat conversation preview helpers.

These helpers maintain participant-specific conversation previews.

Why participant-specific previews exist:
- If participant 1 deletes a message "for me", participant 1 should no longer
  see that message as the conversation preview.
- Participant 2 should still see it.
- Therefore AOS Conversation stores:
    last_message_1 / last_message_at_1 / last_sender_1
    last_message_2 / last_message_at_2 / last_sender_2
"""

from __future__ import annotations

from typing import Any, Dict

import frappe

from .visibility import (
    DELETED_MESSAGE_TEXT,
    get_last_message_at_field,
    get_last_message_field,
    get_last_sender_field,
    is_deleted_for_everyone,
)


def _get_ad_meta_fields() -> list[str]:
    """
    Build a safe list of AOS Ad fields to fetch.

    This mirrors message.py behavior but keeps preview.py independent to avoid
    circular imports.
    """

    fields = ["name"]

    try:
        meta = frappe.get_meta("AOS Ad")
    except Exception:
        return fields

    possible_fields = [
        "title",
        "ad_title",
        "name1",
        "price",
        "currency",
        "status",
        "seller",
    ]

    for fieldname in possible_fields:
        if meta.has_field(fieldname):
            fields.append(fieldname)

    return fields


def _fetch_ads_bulk(ad_ids: list[str]) -> Dict[str, Dict[str, Any]]:
    """
    Fetch minimal ad preview data for building conversation previews.
    """

    if not ad_ids:
        return {}

    unique_ad_ids = list({ad for ad in ad_ids if ad})

    if not unique_ad_ids:
        return {}

    rows = frappe.get_all(
        "AOS Ad",
        filters={"name": ["in", unique_ad_ids]},
        fields=_get_ad_meta_fields(),
    )

    result: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        title = (
            row.get("title")
            or row.get("ad_title")
            or row.get("name1")
            or row.name
        )

        result[row.name] = {
            "id": row.name,
            "title": title,
            "price": row.get("price"),
            "currency": row.get("currency"),
            "status": row.get("status"),
            "seller": row.get("seller"),
        }

    return result


def build_message_preview(
    msg,
    *,
    ad_map: Dict[str, Dict[str, Any]] | None = None,
) -> str:
    """
    Build a display-safe conversation preview for one message.
    """

    if is_deleted_for_everyone(msg):
        return DELETED_MESSAGE_TEXT

    content = (getattr(msg, "content", None) or "").strip()
    if content:
        return content

    ad = getattr(msg, "ad", None)
    if ad:
        ad_preview = (ad_map or {}).get(ad)
        title = ad_preview.get("title") if ad_preview else None
        return title or "[Ad]"

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
        deleted_field = "deleted_for_1"
    elif user == conv.participant_2:
        deleted_field = "deleted_for_2"
    else:
        return None

    rows = frappe.db.sql(
        f"""
        SELECT
            name,
            conversation,
            sender,
            content,
            message_type,
            ad,
            reply_to_message,
            has_attachments,
            is_edited,
            edited_at,
            deleted_for_everyone,
            deleted_for_everyone_at,
            deleted_for_1,
            deleted_for_1_at,
            deleted_for_2,
            deleted_for_2_at,
            delivered_to_receiver_at,
            read_by_receiver_at,
            creation
        FROM `tabAOS Message`
        WHERE
            conversation = %(conversation_id)s
            AND IFNULL({deleted_field}, 0) = 0
        ORDER BY creation DESC
        LIMIT 1
        """,
        {"conversation_id": conversation_id},
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

    ad_map = _fetch_ads_bulk([latest.ad]) if latest.ad else {}

    preview = build_message_preview(
        latest,
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
    Fast path for send_message.

    A newly sent message is visible to both participants, so both previews can be
    updated directly without scanning the message table.
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
        },
        update_modified=False,
    )
