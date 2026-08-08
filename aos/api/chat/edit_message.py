"""
Edit message API (implementation).

Handles:
- edit_message

Rules:
- Only the original sender can edit their message.
- System messages cannot be edited.
- Media-only messages cannot be edited.
- Text, mixed, and ad messages can be edited.
- Deleted-for-everyone messages cannot be edited.
- Messages deleted for the current sender cannot be edited.
- Editing updates content, is_edited, edited_at, and original_content.
- Conversation previews are recomputed after edit using participant-specific
  last_message_1 / last_message_2 fields.
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception

from .constants import EDIT_MESSAGE_LIMIT_PER_MINUTE_PER_USER

from .message import (
    _fetch_ads_bulk,
    _fetch_reply_messages_bulk,
    _fetch_shorts_bulk,
    _fetch_lives_bulk,
    _fetch_users,
    _get_receiver,
    _is_deleted_for_everyone,
    _serialize_attachments_bulk,
    _serialize_message,
)

from .preview import recompute_conversation_previews
from aos.services.chat.events import publish_after_commit
from aos.services.chat.repository import lock_conversations, lock_messages
from .visibility import get_deleted_for_user_field


EDITABLE_MESSAGE_TYPES = {
    "text",
    "mixed",
    "ad",
}


def _get_message_for_edit(message_id: str):
    """
    Fetch the message and its conversation participants.
    """

    return frappe.db.sql(
        """
        SELECT
            m.name,
            m.conversation,
            m.sender,
            m.content,
            m.message_type,
            m.ad,
            m.short,
            m.live,
            m.reply_to_message,
            m.has_attachments,

            m.is_forwarded,
            m.forwarded_from_message,
            m.forwarded_from_conversation,

            m.is_edited,
            m.edited_at,
            m.original_content,

            m.deleted_for_everyone,
            m.deleted_for_everyone_at,
            m.deleted_for_1,
            m.deleted_for_1_at,
            m.deleted_for_2,
            m.deleted_for_2_at,

            m.delivered_to_receiver_at,
            m.read_by_receiver_at,
            m.creation,

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


def _is_deleted_for_current_user(msg, current_user: str) -> bool:
    """
    Whether this message has been deleted-for-me by the current user.

    If the sender deleted the message for themselves, they should not be able
    to edit it through the API anymore because it is no longer visible to them.
    """

    fieldname = get_deleted_for_user_field(msg, current_user)
    return bool(getattr(msg, fieldname, 0))


def _serialize_edited_message(
    msg,
    *,
    current_user: str,
) -> Dict[str, Any]:
    """
    Serialize edited message using the same shape as send/list message.
    """

    attachments_map = _serialize_attachments_bulk([msg.name], current_user=current_user)

    reply_map = _fetch_reply_messages_bulk(
        [msg.reply_to_message] if msg.reply_to_message else []
    )

    user_ids: List[str] = [msg.sender]

    for replied in reply_map.values():
        if replied.sender:
            user_ids.append(replied.sender)

    ad_ids: List[str] = []

    if msg.ad and not _is_deleted_for_everyone(msg):
        ad_ids.append(msg.ad)

    for replied in reply_map.values():
        if replied.ad and not _is_deleted_for_everyone(replied):
            ad_ids.append(replied.ad)

    user_map = _fetch_users(user_ids, viewer=current_user)
    ad_map = _fetch_ads_bulk(ad_ids, viewer=current_user)

    short_ids: List[str] = []
    live_ids: List[str] = []
    if getattr(msg, "short", None) and not _is_deleted_for_everyone(msg):
        short_ids.append(msg.short)
    if getattr(msg, "live", None) and not _is_deleted_for_everyone(msg):
        live_ids.append(msg.live)
    for replied in reply_map.values():
        if _is_deleted_for_everyone(replied):
            continue
        if getattr(replied, "short", None):
            short_ids.append(replied.short)
        if getattr(replied, "live", None):
            live_ids.append(replied.live)

    return _serialize_message(
        msg,
        attachments_map=attachments_map,
        user_map=user_map,
        ad_map=ad_map,
        short_map=_fetch_shorts_bulk(short_ids, viewer=current_user),
        live_map=_fetch_lives_bulk(live_ids, viewer=current_user),
        current_user=current_user,
        reply_map=reply_map,
        is_starred=False,
        reactions=[],
        my_reaction=None,
    )


def edit_message_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "edit_message", current_user),
        ttl_seconds=60,
        limit=EDIT_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many edit requests. Please slow down.",
    )
    if rl:
        return rl

    message_id = kwargs.get("message_id")
    content = (kwargs.get("content") or "").strip()

    if not message_id:
        return fail("message_id is required.", error="VALIDATION_ERROR")

    if not content:
        return fail("content is required.", error="VALIDATION_ERROR")

    try:
        conversation_id = frappe.db.get_value("AOS Message", message_id, "conversation")
        if not conversation_id:
            return fail("Message not found.", error="NOT_FOUND")

        lock_conversations([conversation_id])
        if message_id not in set(lock_messages([message_id])):
            return fail("Message not found.", error="NOT_FOUND")

        rows = _get_message_for_edit(message_id)
        if not rows:
            return fail("Message not found.", error="NOT_FOUND")

        msg = rows[0]

        if current_user not in (msg.participant_1, msg.participant_2):
            return fail("Not allowed.", error="PERMISSION_DENIED")

        if msg.sender != current_user:
            return fail(
                "You can only edit your own messages.",
                error="PERMISSION_DENIED",
            )

        if _is_deleted_for_everyone(msg):
            return fail(
                "Deleted messages cannot be edited.",
                error="VALIDATION_ERROR",
            )

        if _is_deleted_for_current_user(msg, current_user):
            return fail(
                "You cannot edit a message you deleted for yourself.",
                error="VALIDATION_ERROR",
            )

        if msg.message_type not in EDITABLE_MESSAGE_TYPES:
            return fail(
                "This message type cannot be edited.",
                error="VALIDATION_ERROR",
            )

        if msg.message_type == "ad" and not msg.ad:
            return fail(
                "Invalid ad message.",
                error="VALIDATION_ERROR",
            )

        old_content = (msg.content or "").strip()

        if old_content == content:
            return fail(
                "Message content has not changed.",
                error="VALIDATION_ERROR",
            )

        now = now_datetime()
        original_content = msg.original_content or old_content

        frappe.db.set_value(
            "AOS Message",
            message_id,
            {
                "content": content,
                "is_edited": 1,
                "edited_at": now,
                "original_content": original_content,
            },
            update_modified=True,
        )

        # Update in-memory row for serialization.
        msg.content = content
        msg.is_edited = 1
        msg.edited_at = now
        msg.original_content = original_content

        # Recompute both participant-specific previews.
        # This handles cases where this edited message is the latest visible
        # message for one participant but not the other due to delete-for-me.
        recompute_conversation_previews(msg.conversation)

        receiver = _get_receiver(msg, current_user)

        serialized_for_sender = _serialize_edited_message(
            msg,
            current_user=current_user,
        )

        serialized_for_receiver = _serialize_edited_message(
            msg,
            current_user=receiver,
        )

        realtime_payload = {
            "conversation_id": msg.conversation,
            "message": serialized_for_receiver,
        }

        publish_after_commit(
            event="aos_message_edited",
            message=realtime_payload,
            user=receiver,
        )

        return ok("Message edited.", data=serialized_for_sender)

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error("Chat operation failed.", "AOS Edit Message Failed")
        return fail("Failed to edit message.", error="INTERNAL_ERROR")
