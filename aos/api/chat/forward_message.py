"""
Forward message API (implementation).

Handles:
- forward_message

Behavior:
- Forward one visible source message to one or more target conversations.
- Creates a new AOS Message in each target conversation.
- Sender is always the current user.
- Does not copy reactions, stars, read/delivered status, edits, delete flags, or replies.
- Copies content, ad reference, and attachments.
- Marks new messages as forwarded using:
    is_forwarded
    forwarded_from_message
    forwarded_from_conversation
- Reactivates target conversations for both participants because a new message
  should make a previously hidden/deleted conversation visible again.
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from aos.services.notification_service import NotificationService

from .constants import FORWARD_MESSAGE_LIMIT_PER_MINUTE_PER_USER

from .message import (
    _determine_message_type,
    _fetch_ads_bulk,
    _fetch_reply_messages_bulk,
    _fetch_shorts_bulk,
    _fetch_users,
    _get_receiver,
    _is_deleted_for_everyone,
    _message_preview,
    _serialize_attachments_bulk,
    _serialize_message,
    _validate_short_reference,
)

from .preview import set_conversation_preview_for_new_message
from .presence import publish_presence_update_to_peers
from .visibility import get_deleted_for_user_field


def _normalize_target_conversation_ids(kwargs) -> List[str]:
    """
    Accept either:
    - target_conversation_id: "CONV-1"
    - target_conversation_ids: ["CONV-1", "CONV-2"]
    """

    single = kwargs.get("target_conversation_id")
    multiple = kwargs.get("target_conversation_ids")

    if isinstance(multiple, list):
        values = [str(v).strip() for v in multiple if str(v or "").strip()]
    elif isinstance(single, str) and single.strip():
        values = [single.strip()]
    else:
        values = []

    # Deduplicate while preserving order.
    return list(dict.fromkeys(values))


def _get_source_message(message_id: str):
    """
    Fetch source message together with source conversation participants.
    """

    rows = frappe.db.sql(
        """
        SELECT
            m.name,
            m.conversation,
            m.sender,
            m.content,
            m.message_type,
            m.ad,
            m.short,
            m.reply_to_message,
            m.has_attachments,
            m.is_forwarded,
            m.forwarded_from_message,
            m.forwarded_from_conversation,
            m.is_edited,
            m.edited_at,

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

    return rows[0] if rows else None


def _get_target_conversations(conversation_ids: List[str]) -> Dict[str, frappe._dict]:
    if not conversation_ids:
        return {}

    rows = frappe.get_all(
        "AOS Conversation",
        filters={"name": ["in", conversation_ids]},
        fields=["name", "participant_1", "participant_2"],
    )

    return {row.name: row for row in rows}


def _fetch_source_attachments(message_id: str) -> List[frappe._dict]:
    if not message_id:
        return []

    return frappe.get_all(
        "AOS Message Attachment",
        filters={"message": message_id},
        fields=["media", "file", "file_type", "sort_order"],
        order_by="sort_order asc",
    )


def _validate_source_message_can_be_forwarded(source, current_user: str):
    if current_user not in (source.participant_1, source.participant_2):
        return fail("Not allowed.", code="PERMISSION_DENIED")

    if _is_deleted_for_everyone(source):
        return fail(
            "Deleted messages cannot be forwarded.",
            code="VALIDATION_ERROR",
        )

    delete_field = get_deleted_for_user_field(source, current_user)

    if bool(getattr(source, delete_field, 0)):
        return fail(
            "You cannot forward a message deleted for you.",
            code="VALIDATION_ERROR",
        )

    if source.message_type == "system":
        return fail(
            "System messages cannot be forwarded.",
            code="VALIDATION_ERROR",
        )

    return None


def _validate_target_conversations(
    *,
    target_conversation_ids: List[str],
    target_conversation_map: Dict[str, frappe._dict],
    current_user: str,
):
    missing = [
        conv_id
        for conv_id in target_conversation_ids
        if conv_id not in target_conversation_map
    ]

    if missing:
        return fail(
            "One or more target conversations were not found.",
            code="NOT_FOUND",
            data={"missing_conversation_ids": missing},
        )

    not_allowed = []

    for conv_id in target_conversation_ids:
        conv = target_conversation_map[conv_id]

        if current_user not in (conv.participant_1, conv.participant_2):
            not_allowed.append(conv_id)

    if not_allowed:
        return fail(
            "You are not allowed to forward to one or more conversations.",
            code="PERMISSION_DENIED",
            data={"conversation_ids": not_allowed},
        )

    return None



def _validate_forwarded_short_access(
    *,
    short: str | None,
    target_conversation_ids: List[str],
    target_conversation_map: Dict[str, frappe._dict],
    current_user: str,
):
    """Ensure a forwarded short can be viewed by all target recipients."""
    if not short:
        return None

    recipients: List[str] = []
    for conv_id in target_conversation_ids:
        conv = target_conversation_map.get(conv_id)
        if not conv:
            continue
        recipients.append(_get_receiver(conv, current_user))

    return _validate_short_reference(
        short,
        viewer=current_user,
        recipients=recipients,
    )

def _copy_attachments(
    *,
    source_attachments: List[frappe._dict],
    target_message_id: str,
) -> int:
    """Copy attachment rows to the forwarded message.

    For media-backed attachments, the same private AOS Media Object is referenced
    by the forwarded message row. Read permission is granted through the target
    conversation membership because MediaService checks AOS Message Attachment
    rows, not only the media object's single attached_name.
    """

    has_attachments = 0

    for index, att in enumerate(source_attachments):
        media_id = getattr(att, "media", None)

        if not media_id or not frappe.db.exists("AOS Media Object", media_id):
            continue

        doc = {
            "doctype": "AOS Message Attachment",
            "message": target_message_id,
            "media": media_id,
            "file_type": att.file_type,
            "sort_order": att.sort_order if att.sort_order is not None else index,
        }

        frappe.get_doc(doc).insert(ignore_permissions=True)
        has_attachments = 1

    return has_attachments


def _create_forwarded_message(
    *,
    source,
    target_conversation_id: str,
    current_user: str,
    source_attachments: List[frappe._dict],
):
    content = (source.content or "").strip()
    ad = source.ad
    short = getattr(source, "short", None)

    message_type = _determine_message_type(
        content=content,
        attachments=[
            {
                "media": getattr(att, "media", None),
                "file": getattr(att, "file", None),
                "file_type": att.file_type,
            }
            for att in source_attachments
            if (getattr(att, "media", None) or getattr(att, "file", None)) and att.file_type
        ],
        ad=ad,
        short=short,
    )

    msg = frappe.new_doc("AOS Message")
    msg.conversation = target_conversation_id
    msg.sender = current_user
    msg.message_type = message_type
    msg.content = content or None

    if ad:
        msg.ad = ad

    if short:
        msg.short = short

    msg.insert(ignore_permissions=True)

    has_attachments = _copy_attachments(
        source_attachments=source_attachments,
        target_message_id=msg.name,
    )

    updates = {
        "is_forwarded": 1,
        "forwarded_from_message": source.name,
        "forwarded_from_conversation": source.conversation,
    }

    if has_attachments:
        updates["has_attachments"] = 1
        msg.has_attachments = 1
    else:
        msg.has_attachments = 0

    frappe.db.set_value(
        "AOS Message",
        msg.name,
        updates,
        update_modified=False,
    )

    msg.is_forwarded = 1
    msg.forwarded_from_message = source.name
    msg.forwarded_from_conversation = source.conversation

    return msg


def _increment_unread_for_receiver(
    *,
    conv,
    current_user: str,
):
    unread_field = (
        "unread_count_2"
        if current_user == conv.participant_1
        else "unread_count_1"
    )

    frappe.db.sql(
        f"""
        UPDATE `tabAOS Conversation`
        SET
            is_active_1 = 1,
            is_active_2 = 1,
            {unread_field} = COALESCE({unread_field}, 0) + 1
        WHERE name = %s
        """,
        (conv.name,),
    )


def _serialize_forwarded_message(
    msg,
    *,
    current_user: str,
) -> Dict[str, Any]:
    attachments_map = _serialize_attachments_bulk([msg.name], current_user=current_user)

    reply_map = _fetch_reply_messages_bulk(
        [msg.reply_to_message] if getattr(msg, "reply_to_message", None) else []
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

    short_ids: List[str] = []

    if getattr(msg, "short", None) and not _is_deleted_for_everyone(msg):
        short_ids.append(msg.short)

    for replied in reply_map.values():
        if getattr(replied, "short", None) and not _is_deleted_for_everyone(replied):
            short_ids.append(replied.short)

    user_map = _fetch_users(user_ids)
    ad_map = _fetch_ads_bulk(ad_ids)
    short_map = _fetch_shorts_bulk(short_ids, viewer=current_user)

    return _serialize_message(
        msg,
        attachments_map=attachments_map,
        user_map=user_map,
        ad_map=ad_map,
        short_map=short_map,
        current_user=current_user,
        reply_map=reply_map,
        is_starred=False,
        reactions=[],
        my_reaction=None,
    )


def forward_message_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:forward:user:{current_user}",
        ttl_seconds=60,
        limit=FORWARD_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many forward requests. Please slow down.",
    )
    if rl:
        return rl

    message_id = kwargs.get("message_id")
    target_conversation_ids = _normalize_target_conversation_ids(kwargs)

    if not message_id:
        return fail("message_id is required.", code="VALIDATION_ERROR")

    if not target_conversation_ids:
        return fail(
            "target_conversation_id or target_conversation_ids is required.",
            code="VALIDATION_ERROR",
        )

    try:
        source = _get_source_message(message_id)

        if not source:
            return fail("Message not found.", code="NOT_FOUND")

        source_error = _validate_source_message_can_be_forwarded(
            source,
            current_user,
        )
        if source_error:
            return source_error

        target_conversation_map = _get_target_conversations(target_conversation_ids)

        target_error = _validate_target_conversations(
            target_conversation_ids=target_conversation_ids,
            target_conversation_map=target_conversation_map,
            current_user=current_user,
        )
        if target_error:
            return target_error

        short_error = _validate_forwarded_short_access(
            short=getattr(source, "short", None),
            target_conversation_ids=target_conversation_ids,
            target_conversation_map=target_conversation_map,
            current_user=current_user,
        )
        if short_error:
            return short_error

        source_attachments = _fetch_source_attachments(source.name)

        now = now_datetime()
        forwarded_messages: List[Dict[str, Any]] = []

        source_ad_map = _fetch_ads_bulk([source.ad]) if source.ad else {}
        source_short_map = _fetch_shorts_bulk([source.short], viewer=current_user) if getattr(source, "short", None) else {}

        preview = _message_preview(
            content=source.content,
            has_attachments=1 if source_attachments else 0,
            ad=source.ad,
            short=getattr(source, "short", None),
            ad_preview=source_ad_map.get(source.ad) if source.ad else None,
            short_preview=source_short_map.get(source.short) if getattr(source, "short", None) else None,
        )

        for target_conversation_id in target_conversation_ids:
            target_conv = target_conversation_map[target_conversation_id]

            msg = _create_forwarded_message(
                source=source,
                target_conversation_id=target_conversation_id,
                current_user=current_user,
                source_attachments=source_attachments,
            )

            set_conversation_preview_for_new_message(
                conversation_id=target_conversation_id,
                sender=current_user,
                preview=preview,
                sent_at=now,
            )

            _increment_unread_for_receiver(
                conv=target_conv,
                current_user=current_user,
            )

            serialized = _serialize_forwarded_message(
                msg,
                current_user=current_user,
            )

            receiver = _get_receiver(target_conv, current_user)

            realtime_payload = {
                "conversation_id": target_conversation_id,
                "message": serialized,
            }

            frappe.publish_realtime(
                event="aos_new_message",
                message=realtime_payload,
                user=receiver,
            )

            NotificationService.notify_new_message(
                user=receiver,
                sender=current_user,
                conversation_id=target_conversation_id,
                preview=preview,
            )

            forwarded_messages.append(
                {
                    "conversation_id": target_conversation_id,
                    "message": serialized,
                }
            )

        publish_presence_update_to_peers(current_user)

        return ok(
            "Message forwarded.",
            data={
                "source_message_id": source.name,
                "forwarded_count": len(forwarded_messages),
                "messages": forwarded_messages,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Forward Message Failed",
        )
        frappe.db.rollback()
        return fail("Failed to forward message.", code="INTERNAL_ERROR")
