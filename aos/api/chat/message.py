"""
Message APIs (implementation).

Handles:
- send_message
- list_messages
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from aos.services.notification_service import NotificationService

from .constants import (
    SEND_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
    LIST_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
)

from .presence import publish_presence_update_to_peers


# Helpers
def _clean_int(value, default: int, *, min_value: int, max_value: int) -> int:
    """
    Safely parse integer inputs like pagination limit.
    """

    try:
        parsed = int(value)
    except Exception:
        parsed = default

    if parsed < min_value:
        return min_value

    if parsed > max_value:
        return max_value

    return parsed


def _get_conversation_row(conv_id: str):
    return frappe.db.get_value(
        "AOS Conversation",
        conv_id,
        ["participant_1", "participant_2"],
        as_dict=True,
    )


def _validate_sender(conv, sender: str) -> bool:
    return sender in (conv.participant_1, conv.participant_2)


def _get_receiver(conv, sender: str) -> str:
    return (
        conv.participant_2
        if conv.participant_1 == sender
        else conv.participant_1
    )


def _fetch_users(users: List[str]) -> Dict[str, frappe._dict]:
    """
    Fetch lightweight user profile info for display.
    """

    if not users:
        return {}

    rows = frappe.get_all(
        "User",
        filters={"name": ["in", users]},
        fields=["name", "full_name", "user_image"],
    )

    return {row.name: row for row in rows}


def _serialize_user(user_id: str, user_map: Dict[str, frappe._dict]) -> Dict[str, Any]:
    """
    Build display fields for a user.
    """

    user = user_map.get(user_id)

    return {
        "sender": user_id,
        "sender_display_name": (
            user.full_name
            if user and user.full_name
            else user_id
        ),
        "sender_avatar": user.user_image if user else None,
    }


def _serialize_attachments_bulk(message_ids: List[str]) -> Dict[str, List[Dict]]:
    if not message_ids:
        return {}

    rows = frappe.get_all(
        "AOS Message Attachment",
        filters={"message": ["in", message_ids]},
        fields=["message", "file", "file_type", "sort_order"],
        order_by="sort_order asc",
    )

    if not rows:
        return {}

    file_ids = [r.file for r in rows if r.file]

    if not file_ids:
        return {}

    files = frappe.get_all(
        "File",
        filters={"name": ["in", file_ids]},
        fields=["name", "file_url"],
    )

    file_map = {f.name: f.file_url for f in files}

    grouped: Dict[str, List[Dict]] = {}

    for r in rows:
        file_url = file_map.get(r.file)
        if not file_url:
            continue

        grouped.setdefault(r.message, []).append(
            {
                "url": file_url,
                "type": r.file_type,
                "sort_order": r.sort_order,
            }
        )

    return grouped


def _get_ad_meta_fields() -> List[str]:
    """
    Build a safe list of AOS Ad fields to fetch.

    This avoids breaking if a field name differs between environments.
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


def _fetch_ad_thumbnails(ad_ids: List[str]) -> Dict[str, str | None]:
    """
    Fetch primary/first thumbnail for ads.

    Uses AOS Ad Image child table, same idea as Shorts:
    primary image first, then sort_order, then idx.
    """

    if not ad_ids:
        return {}

    rows = frappe.db.sql(
        """
        SELECT
            adi.parent AS ad,
            adi.image AS image
        FROM `tabAOS Ad Image` adi
        INNER JOIN (
            SELECT
                ranked.parent,
                MIN(ranked.rank_key) AS best_rank
            FROM (
                SELECT
                    parent,
                    CONCAT(
                        LPAD(CASE WHEN IFNULL(is_primary, 0) = 1 THEN 0 ELSE 1 END, 2, '0'),
                        '-',
                        LPAD(IFNULL(sort_order, 999999), 8, '0'),
                        '-',
                        LPAD(IFNULL(idx, 999999), 8, '0')
                    ) AS rank_key
                FROM `tabAOS Ad Image`
                WHERE
                    parent IN %(ad_ids)s
                    AND parenttype = 'AOS Ad'
                    AND parentfield = 'images'
                    AND image IS NOT NULL
                    AND image != ''
            ) ranked
            GROUP BY ranked.parent
        ) best
            ON best.parent = adi.parent
            AND CONCAT(
                LPAD(CASE WHEN IFNULL(adi.is_primary, 0) = 1 THEN 0 ELSE 1 END, 2, '0'),
                '-',
                LPAD(IFNULL(adi.sort_order, 999999), 8, '0'),
                '-',
                LPAD(IFNULL(adi.idx, 999999), 8, '0')
            ) = best.best_rank
        WHERE
            adi.parent IN %(ad_ids)s
            AND adi.parenttype = 'AOS Ad'
            AND adi.parentfield = 'images'
        """,
        {"ad_ids": tuple(ad_ids)},
        as_dict=True,
    )

    return {row.ad: row.image for row in rows}


def _fetch_ads_bulk(ad_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Fetch lightweight ad previews in bulk.

    Returns:
        {
            "AD-001": {
                "id": "AD-001",
                "title": "...",
                "price": 1200,
                "currency": "KES",
                "status": "Active",
                "seller": "...",
                "thumbnail": "/files/..."
            }
        }
    """

    if not ad_ids:
        return {}

    unique_ad_ids = list({ad for ad in ad_ids if ad})

    if not unique_ad_ids:
        return {}

    fields = _get_ad_meta_fields()

    rows = frappe.get_all(
        "AOS Ad",
        filters={"name": ["in", unique_ad_ids]},
        fields=fields,
    )

    thumbnails = _fetch_ad_thumbnails(unique_ad_ids)

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
            "thumbnail": thumbnails.get(row.name),
        }

    return result


def _validate_ad_reference(ad: str | None):
    """
    Validate ad reference only when provided.
    """

    if not ad:
        return None

    if not frappe.db.exists("AOS Ad", ad):
        return fail("Invalid ad reference.", code="VALIDATION_ERROR")

    return None


def _determine_message_type(
    *,
    content: str,
    attachments: List[Dict],
    ad: str | None,
) -> str:
    """
    Determine message type.

    Supported shape:
    - text only       -> text
    - media only      -> media
    - ad only         -> ad
    - combinations    -> mixed
    """

    parts = 0

    if content:
        parts += 1

    if attachments:
        parts += 1

    if ad:
        parts += 1

    if parts > 1:
        return "mixed"

    if ad:
        return "ad"

    if attachments:
        return "media"

    return "text"


def _message_preview(
    *,
    content: str | None,
    has_attachments: int,
    ad: str | None,
    ad_preview: Dict[str, Any] | None = None,
) -> str:
    """
    Build last_message / notification preview.
    """

    if content:
        return content

    if ad:
        title = ad_preview.get("title") if ad_preview else None
        return title or "[Ad]"

    if has_attachments:
        return "[Attachment]"

    return "[Message]"


def _serialize_message(
    msg,
    *,
    attachments_map: Dict[str, List[Dict]],
    user_map: Dict[str, frappe._dict],
    ad_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Serialize one message into the API/realtime shape.
    """

    user_payload = _serialize_user(msg.sender, user_map)

    return {
        "id": msg.name,
        "sender": user_payload["sender"],
        "sender_display_name": user_payload["sender_display_name"],
        "sender_avatar": user_payload["sender_avatar"],
        "content": msg.content,
        "message_type": msg.message_type,
        "ad": msg.ad,
        "ad_preview": ad_map.get(msg.ad) if msg.ad else None,
        "has_attachments": msg.has_attachments or 0,
        "attachments": attachments_map.get(msg.name, []),
        "delivered_at": getattr(msg, "delivered_to_receiver_at", None),
        "read_at": getattr(msg, "read_by_receiver_at", None),
        "created_at": msg.creation,
    }


# send_message
def send_message_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:send:user:{current_user}",
        ttl_seconds=60,
        limit=SEND_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many messages. Please slow down.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")
    content = (kwargs.get("content") or "").strip()
    ad = kwargs.get("ad")
    attachments = kwargs.get("attachments") or []

    if not conv_id:
        return fail("conversation_id is required.", code="VALIDATION_ERROR")

    if not isinstance(attachments, list):
        return fail("attachments must be a list.", code="VALIDATION_ERROR")

    try:
        conv = _get_conversation_row(conv_id)
        if not conv:
            return fail("Conversation not found.", code="NOT_FOUND")

        if not _validate_sender(conv, current_user):
            return fail("Not allowed.", code="PERMISSION_DENIED")

        if not content and not attachments and not ad:
            return fail(
                "Message must have content, attachments, or an ad.",
                code="VALIDATION_ERROR",
            )

        ad_error = _validate_ad_reference(ad)
        if ad_error:
            return ad_error

        receiver = _get_receiver(conv, current_user)

        message_type = _determine_message_type(
            content=content,
            attachments=attachments,
            ad=ad,
        )

        now = now_datetime()

        # Create message.
        msg = frappe.new_doc("AOS Message")
        msg.conversation = conv_id
        msg.sender = current_user
        msg.message_type = message_type
        msg.content = content or None

        if ad:
            msg.ad = ad

        msg.insert(ignore_permissions=True)

        # Attachments.
        has_attachments = 0

        for i, att in enumerate(attachments):
            if not isinstance(att, dict):
                frappe.db.rollback()
                return fail(
                    "Invalid attachment payload.",
                    code="VALIDATION_ERROR",
                )

            file_id = att.get("file")
            file_type = att.get("file_type")

            if not file_id or not file_type:
                frappe.db.rollback()
                return fail(
                    "Invalid attachment payload.",
                    code="VALIDATION_ERROR",
                )

            if not frappe.db.exists("File", file_id):
                frappe.db.rollback()
                return fail(
                    "Invalid file reference.",
                    code="VALIDATION_ERROR",
                )

            frappe.get_doc(
                {
                    "doctype": "AOS Message Attachment",
                    "message": msg.name,
                    "file": file_id,
                    "file_type": file_type,
                    "sort_order": i,
                }
            ).insert(ignore_permissions=True)

            has_attachments = 1

        if has_attachments:
            msg.db_set("has_attachments", 1, update_modified=False)
            msg.has_attachments = 1
        else:
            msg.has_attachments = 0

        # Build rich maps for response/realtime/preview.
        attachments_map = _serialize_attachments_bulk([msg.name])
        user_map = _fetch_users([current_user])
        ad_map = _fetch_ads_bulk([ad]) if ad else {}

        serialized = _serialize_message(
            msg,
            attachments_map=attachments_map,
            user_map=user_map,
            ad_map=ad_map,
        )

        preview = _message_preview(
            content=msg.content,
            has_attachments=has_attachments,
            ad=ad,
            ad_preview=ad_map.get(ad) if ad else None,
        )

        # Update conversation.
        if current_user == conv.participant_1:
            unread_field = "unread_count_2"
        else:
            unread_field = "unread_count_1"

        frappe.db.sql(
            f"""
            UPDATE `tabAOS Conversation`
            SET
                last_message = %s,
                last_message_at = %s,
                last_sender = %s,
                {unread_field} = COALESCE({unread_field}, 0) + 1
            WHERE name = %s
            """,
            (
                preview,
                now,
                current_user,
                conv_id,
            ),
        )

        # Realtime.
        realtime_payload = {
            "conversation_id": conv_id,
            "message": serialized,
        }

        frappe.publish_realtime(
            event="aos_new_message",
            message=realtime_payload,
            user=receiver,
        )

        # Notification.
        NotificationService.notify_new_message(
            user=receiver,
            sender=current_user,
            conversation_id=conv_id,
            preview=preview,
        )

        # Presence trigger.
        publish_presence_update_to_peers(current_user)

        return ok("Message sent.", data=serialized)

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Send Message Failed",
        )
        frappe.db.rollback()
        return fail("Failed to send message.", code="INTERNAL_ERROR")


# list_messages
def list_messages_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:list_msgs:user:{current_user}",
        ttl_seconds=60,
        limit=LIST_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")

    limit = _clean_int(
        kwargs.get("limit"),
        default=30,
        min_value=1,
        max_value=100,
    )

    before = kwargs.get("before")

    if not conv_id:
        return fail("conversation_id is required.", code="VALIDATION_ERROR")

    try:
        conv = _get_conversation_row(conv_id)
        if not conv:
            return fail("Conversation not found.", code="NOT_FOUND")

        if current_user not in (conv.participant_1, conv.participant_2):
            return fail("Not allowed.", code="PERMISSION_DENIED")

        filters: Dict[str, Any] = {"conversation": conv_id}

        if before:
            before_creation = frappe.db.get_value(
                "AOS Message",
                before,
                "creation",
            )

            if not before_creation:
                return fail("Invalid 'before' message.", code="VALIDATION_ERROR")

            filters["creation"] = ("<", before_creation)

        messages = frappe.get_all(
            "AOS Message",
            filters=filters,
            fields=[
                "name",
                "sender",
                "content",
                "message_type",
                "ad",
                "has_attachments",
                "delivered_to_receiver_at",
                "read_by_receiver_at",
                "creation",
            ],
            order_by="creation desc",
            limit_page_length=limit,
        )

        if not messages:
            return ok("Messages fetched.", data=[])

        message_ids = [m.name for m in messages]
        sender_ids = list({m.sender for m in messages if m.sender})
        ad_ids = list({m.ad for m in messages if m.ad})

        attachments_map = _serialize_attachments_bulk(message_ids)
        user_map = _fetch_users(sender_ids)
        ad_map = _fetch_ads_bulk(ad_ids)

        results = []

        for m in messages:
            results.append(
                _serialize_message(
                    m,
                    attachments_map=attachments_map,
                    user_map=user_map,
                    ad_map=ad_map,
                )
            )

        return ok("Messages fetched.", data=results)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Messages Failed",
        )
        return fail("Failed to fetch messages.", code="INTERNAL_ERROR")
