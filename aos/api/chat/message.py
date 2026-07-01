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
from aos.api.shared.blocking import ensure_not_blocked
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.api.shared.user_display import get_user_display_map

from aos.services.notification_service import NotificationService
from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
)
from aos.services.seller_response_metrics import (
    enqueue_conversation_response_metrics_refresh,
)

from .constants import (
    SEND_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
    LIST_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
)

from .presence import publish_presence_update_to_peers
from .preview import set_conversation_preview_for_new_message
from .reactions import fetch_message_reaction_summaries, fetch_my_reactions
from .visibility import (
    get_deleted_for_everyone_display_text,
    get_user_delete_sql_condition,
)


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
        ["name", "participant_1", "participant_2"],
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


def _fetch_users(users: List[str]) -> Dict[str, dict]:
    """Fetch display-safe user summaries for messages."""

    return get_user_display_map(users)


def _serialize_user(user_id: str, user_map: Dict[str, dict]) -> Dict[str, Any]:
    """Build display fields for a user."""

    user = user_map.get(user_id)

    return {
        "sender": user_id,
        "sender_display_name": (
            user.get("display_name")
            if user
            else user_id
        ),
        "sender_avatar": user.get("avatar") if user else None,
        "sender_is_deleted": bool(user.get("is_deleted")) if user else False,
        "sender_is_live": (
            bool(user.get("is_live"))
            if user and not bool(user.get("is_deleted"))
            else False
        ),
        "sender_live_id": (
            user.get("live_id")
            if user and not bool(user.get("is_deleted"))
            else None
        ),
        "sender_live_status": (
            user.get("live_status")
            if user and not bool(user.get("is_deleted"))
            else None
        ),
    }


def _infer_attachment_type_from_content_type(
    content_type: str | None,
    fallback: str | None = None,
) -> str:
    content_type = str(content_type or "").split(";", 1)[0].strip().lower()
    fallback = str(fallback or "").strip().lower()

    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    if content_type.startswith("audio/"):
        return "audio"
    if fallback in {"image", "video", "audio", "document"}:
        return fallback
    return "document"


def _serialize_attachments_bulk(
    message_ids: List[str],
    *,
    current_user: str | None = None,
) -> Dict[str, List[Dict]]:
    if not message_ids:
        return {}

    unique_message_ids = list({message_id for message_id in message_ids if message_id})

    if not unique_message_ids:
        return {}

    fields = ["name", "message", "file", "file_type", "sort_order"]
    try:
        if frappe.get_meta("AOS Message Attachment").has_field("media"):
            fields.append("media")
    except Exception:
        pass

    rows = frappe.get_all(
        "AOS Message Attachment",
        filters={"message": ["in", unique_message_ids]},
        fields=fields,
        order_by="sort_order asc",
    )

    if not rows:
        return {}

    grouped: Dict[str, List[Dict]] = {}

    media_ids = list(
        {
            getattr(row, "media", None)
            for row in rows
            if getattr(row, "media", None)
        }
    )

    media_map: Dict[str, frappe._dict] = {}
    media_urls: Dict[str, str] = {}

    if media_ids:
        media_rows = frappe.get_all(
            "AOS Media Object",
            filters={"name": ["in", media_ids]},
            fields=[
                "name",
                "purpose",
                "status",
                "visibility",
                "original_filename",
                "content_type",
                "size_bytes",
                "width",
                "height",
                "duration_seconds",
                "public_url",
            ],
        )
        media_map = {media.name: media for media in media_rows}

        service = MediaService()
        for media_id in media_ids:
            try:
                media_urls[media_id] = service.get_url(
                    media_id=media_id,
                    user=current_user,
                )
            except (MediaNotFoundError, MediaPermissionError, MediaValidationError):
                # The caller should already have checked conversation visibility.
                # If a stale/invalid media row slips through, skip exposing it.
                continue
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "AOS Chat Attachment Media URL Failed",
                )
                continue

    legacy_file_ids = [
        row.file for row in rows if getattr(row, "file", None) and not getattr(row, "media", None)
    ]
    legacy_file_map: Dict[str, str] = {}

    if legacy_file_ids:
        files = frappe.get_all(
            "File",
            filters={"name": ["in", list(set(legacy_file_ids))]},
            fields=["name", "file_url"],
        )
        legacy_file_map = {f.name: f.file_url for f in files}

    for row in rows:
        media_id = getattr(row, "media", None)

        if media_id:
            media = media_map.get(media_id)
            url = media_urls.get(media_id)
            if not media or not url:
                continue

            file_type = _infer_attachment_type_from_content_type(
                media.content_type,
                row.file_type,
            )

            grouped.setdefault(row.message, []).append(
                {
                    "id": row.name,
                    "media": media_id,
                    "media_id": media_id,
                    "file": getattr(row, "file", None),
                    "url": url,
                    "type": file_type,
                    "file_type": file_type,
                    "sort_order": row.sort_order,
                    "filename": media.original_filename,
                    "content_type": media.content_type,
                    "size_bytes": media.size_bytes,
                    "width": media.width,
                    "height": media.height,
                    "duration_seconds": media.duration_seconds,
                    "visibility": media.visibility,
                }
            )
            continue

        # Legacy Frappe File fallback for old messages only.
        file_url = legacy_file_map.get(getattr(row, "file", None))
        if not file_url:
            continue

        grouped.setdefault(row.message, []).append(
            {
                "id": row.name,
                "media": None,
                "media_id": None,
                "file": row.file,
                "url": file_url,
                "type": row.file_type,
                "file_type": row.file_type,
                "sort_order": row.sort_order,
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

    unique_ad_ids = list({ad for ad in ad_ids if ad})

    if not unique_ad_ids:
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
        {"ad_ids": tuple(unique_ad_ids)},
        as_dict=True,
    )

    return {row.ad: row.image for row in rows}


def _fetch_ads_bulk(ad_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Fetch lightweight ad previews in bulk.
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


def _fetch_shorts_bulk(
    short_ids: List[str],
    *,
    viewer: str | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Fetch lightweight short previews in bulk for chat messages.

    Privacy rule:
    - A chat short preview is returned only when the current viewer can still
      view the referenced short.
    - If a short was hidden/deleted/private after it was shared, serializers
      keep the short id but return short_preview=None and short_unavailable=True.
    """
    if not short_ids:
        return {}

    unique_short_ids = list({short_id for short_id in short_ids if short_id})
    if not unique_short_ids:
        return {}

    rows = frappe.get_all(
        "AOS Short",
        filters={"name": ["in", unique_short_ids]},
        fields=[
            "name",
            "owner",
            "caption",
            "thumbnail_url",
            "playback_url",
            "duration_seconds",
            "status",
            "visibility_status",
            "audience",
            "like_count",
            "comment_count",
            "share_count",
            "repost_count",
        ],
    )

    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row.status != "ready" or row.visibility_status != "visible":
            continue

        if not _can_view_short(row, current_user=viewer):
            continue

        result[row.name] = {
            "id": row.name,
            "owner": row.owner,
            "caption": row.caption or "",
            "thumbnail_url": row.thumbnail_url,
            "playback_url": row.playback_url,
            "duration_seconds": row.duration_seconds,
            "status": row.status,
            "visibility_status": row.visibility_status,
            "like_count": row.like_count or 0,
            "comment_count": row.comment_count or 0,
            "share_count": row.share_count or 0,
            "repost_count": row.repost_count or 0,
        }

    return result




def _can_view_short(short_row, *, current_user: str | None = None) -> bool:
    """Lazily import Shorts visibility to avoid chat/shorts circular imports.

    Importing aos.api.shorts.visibility at module import time loads the
    shorts package __init__, which imports shorts.share. shorts.share imports
    chat.message helpers, creating a circular import when Frappe resolves
    aos.api.chat.send_message. Keep the dependency local so chat.message can
    finish initializing first.
    """

    from aos.api.shorts.visibility import can_view_short

    return can_view_short(short_row, current_user=current_user)


def _get_short_reference(short: str | None):
    """Fetch the minimum short row needed for chat visibility checks."""
    if not short:
        return None

    return frappe.db.get_value(
        "AOS Short",
        short,
        ["name", "owner", "status", "visibility_status", "audience"],
        as_dict=True,
    )


def _validate_short_reference(
    short: str | None,
    *,
    viewer: str | None = None,
    recipients: List[str] | None = None,
):
    """Validate a short reference before it is attached to a chat message.

    The sender/viewer must be able to view the short, and every recipient in
    the target conversation must also be able to view it. This prevents
    followers/friends/only_me shorts from leaking through chat shares or
    forwarded messages.
    """
    if not short:
        return None

    short_row = _get_short_reference(short)

    if not short_row:
        return fail("Invalid short reference.", code="VALIDATION_ERROR")

    if short_row.status != "ready" or short_row.visibility_status != "visible":
        return fail("Short is not available.", code="VALIDATION_ERROR")

    if not _can_view_short(short_row, current_user=viewer):
        return fail("Short is not available.", code="VALIDATION_ERROR")

    for recipient in recipients or []:
        if not _can_view_short(short_row, current_user=recipient):
            return fail(
                "This short cannot be shared with one or more recipients.",
                code="FORBIDDEN",
            )

    return None


def _short_unavailable_payload(short_id: str | None, short_map: Dict[str, Dict[str, Any]]):
    if not short_id:
        return {"short_preview": None, "short_unavailable": False}

    preview = short_map.get(short_id)
    return {
        "short_preview": preview,
        "short_unavailable": preview is None,
    }


def _fetch_reply_messages_bulk(
    reply_message_ids: List[str],
) -> Dict[str, frappe._dict]:
    """
    Fetch replied messages in bulk for reply previews.

    This avoids N+1 queries when listing messages with replies.
    """

    if not reply_message_ids:
        return {}

    unique_ids = list({message_id for message_id in reply_message_ids if message_id})

    if not unique_ids:
        return {}

    rows = frappe.get_all(
        "AOS Message",
        filters={"name": ["in", unique_ids]},
        fields=[
            "name",
            "sender",
            "content",
            "message_type",
            "ad",
            "short",
            "has_attachments",
            "is_forwarded",
            "forwarded_from_message",
            "forwarded_from_conversation",
            "is_edited",
            "edited_at",
            "deleted_for_everyone",
            "deleted_for_everyone_at",
            "deleted_for_1",
            "deleted_for_1_at",
            "deleted_for_2",
            "deleted_for_2_at",
            "creation",
        ],
    )

    return {row.name: row for row in rows}


def _fetch_starred_message_ids(message_ids: List[str], user: str) -> set[str]:
    """
    Fetch message ids starred by the current viewer.
    """

    if not message_ids or not user:
        return set()

    unique_message_ids = list({message_id for message_id in message_ids if message_id})

    if not unique_message_ids:
        return set()

    rows = frappe.get_all(
        "AOS Message Star",
        filters={
            "message": ["in", unique_message_ids],
            "user": user,
        },
        fields=["message"],
    )

    return {row.message for row in rows}


def _viewer_state(
    *,
    is_starred: bool = False,
    my_reaction: str | None = None,
) -> Dict[str, Any]:
    """
    Viewer-specific state for this message.

    This keeps private/user-specific fields out of the shared message model.
    """

    return {
        "is_starred": bool(is_starred),
        "my_reaction": my_reaction,
    }


def _validate_ad_reference(ad: str | None):
    """
    Validate ad reference only when provided.
    """

    if not ad:
        return None

    if not frappe.db.exists("AOS Ad", ad):
        return fail("Invalid ad reference.", code="VALIDATION_ERROR")

    return None


def _validate_reply_to_message(
    *,
    reply_to_message: str | None,
    conversation_id: str,
):
    """
    Validate reply target only when provided.

    A user can only reply to a message inside the same conversation.
    """

    if not reply_to_message:
        return None

    replied = frappe.db.get_value(
        "AOS Message",
        reply_to_message,
        ["name", "conversation"],
        as_dict=True,
    )

    if not replied:
        return fail("Reply message not found.", code="NOT_FOUND")

    if replied.conversation != conversation_id:
        return fail(
            "You can only reply to a message in the same conversation.",
            code="VALIDATION_ERROR",
        )

    return None


def _determine_message_type(
    *,
    content: str,
    attachments: List[Dict],
    ad: str | None,
    short: str | None = None,
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

    if short:
        parts += 1

    if parts > 1:
        return "mixed"

    if ad:
        return "ad"

    if short:
        return "short"

    if attachments:
        return "media"

    return "text"


def _message_preview(
    *,
    content: str | None,
    has_attachments: int,
    ad: str | None,
    short: str | None = None,
    ad_preview: Dict[str, Any] | None = None,
    short_preview: Dict[str, Any] | None = None,
) -> str:
    """
    Build last_message / notification preview.
    """

    if content:
        return content

    if ad:
        title = ad_preview.get("title") if ad_preview else None
        return title or "[Ad]"

    if short:
        caption = short_preview.get("caption") if short_preview else None
        return caption or "[Short]"

    if has_attachments:
        return "[Attachment]"

    return "[Message]"


def _is_deleted_for_everyone(msg) -> bool:
    return bool(getattr(msg, "deleted_for_everyone", 0))


def _build_deleted_message_payload(
    msg,
    user_payload: Dict[str, Any],
    *,
    current_user: str,
    is_starred: bool = False,
) -> Dict[str, Any]:
    """
    Serialize a globally deleted message as a safe placeholder.

    Important:
    - Do not expose original content.
    - Do not expose ad preview.
    - Do not expose attachments.
    - Do not expose reactions.
    - Display text is viewer-specific:
        sender   -> You deleted this message
        receiver -> This message was deleted
    """

    return {
        "id": msg.name,
        "sender": user_payload["sender"],
        "sender_display_name": user_payload["sender_display_name"],
        "sender_avatar": user_payload["sender_avatar"],
        "content": None,
        "message_type": "deleted",
        "original_message_type": msg.message_type,
        "ad": None,
        "ad_preview": None,
        "short": None,
        "short_preview": None,
        "short_unavailable": False,
        "reply_to_message": getattr(msg, "reply_to_message", None),
        "reply_to": None,
        "has_attachments": 0,
        "attachments": [],
        "reactions": [],
        "is_forwarded": getattr(msg, "is_forwarded", 0) or 0,
        "forwarded_from_message": getattr(msg, "forwarded_from_message", None),
        "forwarded_from_conversation": getattr(
            msg,
            "forwarded_from_conversation",
            None,
        ),
        "is_edited": msg.is_edited or 0,
        "edited_at": getattr(msg, "edited_at", None),
        "is_deleted_for_everyone": 1,
        "deleted_for_everyone_at": getattr(msg, "deleted_for_everyone_at", None),
        "display_text": get_deleted_for_everyone_display_text(
            sender=msg.sender,
            viewer=current_user,
        ),
        "viewer_state": _viewer_state(
            is_starred=is_starred,
            my_reaction=None,
        ),
        "delivered_at": getattr(msg, "delivered_to_receiver_at", None),
        "read_at": getattr(msg, "read_by_receiver_at", None),
        "created_at": msg.creation,
    }


def _build_reply_payload(
    *,
    reply_to_message: str | None,
    reply_map: Dict[str, frappe._dict],
    user_map: Dict[str, frappe._dict],
    ad_map: Dict[str, Dict[str, Any]],
    short_map: Dict[str, Dict[str, Any]] | None = None,
    current_user: str = "",
) -> Dict[str, Any] | None:
    """
    Build lightweight reply preview for frontend rendering.
    """

    if not reply_to_message:
        return None

    short_map = short_map or {}

    replied = reply_map.get(reply_to_message)
    if not replied:
        return None

    replied_user = _serialize_user(replied.sender, user_map)

    if _is_deleted_for_everyone(replied):
        return {
            "id": replied.name,
            "sender": replied_user["sender"],
            "sender_display_name": replied_user["sender_display_name"],
            "sender_avatar": replied_user["sender_avatar"],
            "content": None,
            "message_type": "deleted",
            "original_message_type": replied.message_type,
            "ad": None,
            "ad_preview": None,
            "short": None,
            "short_preview": None,
            "short_unavailable": False,
            "has_attachments": 0,
            "is_forwarded": getattr(replied, "is_forwarded", 0) or 0,
            "forwarded_from_message": getattr(
                replied,
                "forwarded_from_message",
                None,
            ),
            "forwarded_from_conversation": getattr(
                replied,
                "forwarded_from_conversation",
                None,
            ),
            "is_edited": replied.is_edited or 0,
            "edited_at": getattr(replied, "edited_at", None),
            "is_deleted_for_everyone": 1,
            "deleted_for_everyone_at": getattr(
                replied,
                "deleted_for_everyone_at",
                None,
            ),
            "display_text": get_deleted_for_everyone_display_text(
                sender=replied.sender,
                viewer=current_user,
            ),
            "created_at": replied.creation,
        }

    short_id = getattr(replied, "short", None)
    short_payload = _short_unavailable_payload(short_id, short_map)

    return {
        "id": replied.name,
        "sender": replied_user["sender"],
        "sender_display_name": replied_user["sender_display_name"],
        "sender_avatar": replied_user["sender_avatar"],
        "content": replied.content,
        "message_type": replied.message_type,
        "ad": replied.ad,
        "ad_preview": ad_map.get(replied.ad) if replied.ad else None,
        "short": short_id,
        "short_preview": short_payload["short_preview"],
        "short_unavailable": short_payload["short_unavailable"],
        "has_attachments": replied.has_attachments or 0,
        "is_forwarded": getattr(replied, "is_forwarded", 0) or 0,
        "forwarded_from_message": getattr(replied, "forwarded_from_message", None),
        "forwarded_from_conversation": getattr(
            replied,
            "forwarded_from_conversation",
            None,
        ),
        "is_edited": replied.is_edited or 0,
        "edited_at": getattr(replied, "edited_at", None),
        "is_deleted_for_everyone": 0,
        "deleted_for_everyone_at": None,
        "display_text": None,
        "created_at": replied.creation,
    }


def _serialize_message(
    msg,
    *,
    attachments_map: Dict[str, List[Dict]],
    user_map: Dict[str, frappe._dict],
    ad_map: Dict[str, Dict[str, Any]],
    short_map: Dict[str, Dict[str, Any]] | None = None,
    current_user: str = "",
    reply_map: Dict[str, frappe._dict] | None = None,
    is_starred: bool = False,
    reactions: List[Dict[str, Any]] | None = None,
    my_reaction: str | None = None,
) -> Dict[str, Any]:
    """
    Serialize one message into the API/realtime shape.
    """

    short_map = short_map or {}

    user_payload = _serialize_user(msg.sender, user_map)

    if _is_deleted_for_everyone(msg):
        return _build_deleted_message_payload(
            msg,
            user_payload,
            current_user=current_user,
            is_starred=is_starred,
        )

    reply_to_message = getattr(msg, "reply_to_message", None)

    reply_to = _build_reply_payload(
        reply_to_message=reply_to_message,
        reply_map=reply_map or {},
        user_map=user_map,
        ad_map=ad_map,
        short_map=short_map,
        current_user=current_user,
    )

    short_id = getattr(msg, "short", None)
    short_payload = _short_unavailable_payload(short_id, short_map)

    return {
        "id": msg.name,
        "sender": user_payload["sender"],
        "sender_display_name": user_payload["sender_display_name"],
        "sender_avatar": user_payload["sender_avatar"],
        "content": msg.content,
        "message_type": msg.message_type,
        "ad": msg.ad,
        "ad_preview": ad_map.get(msg.ad) if msg.ad else None,
        "short": short_id,
        "short_preview": short_payload["short_preview"],
        "short_unavailable": short_payload["short_unavailable"],
        "reply_to_message": reply_to_message,
        "reply_to": reply_to,
        "has_attachments": msg.has_attachments or 0,
        "attachments": attachments_map.get(msg.name, []),
        "reactions": reactions or [],
        "is_forwarded": getattr(msg, "is_forwarded", 0) or 0,
        "forwarded_from_message": getattr(msg, "forwarded_from_message", None),
        "forwarded_from_conversation": getattr(
            msg,
            "forwarded_from_conversation",
            None,
        ),
        "is_edited": msg.is_edited or 0,
        "edited_at": getattr(msg, "edited_at", None),
        "is_deleted_for_everyone": 0,
        "deleted_for_everyone_at": None,
        "display_text": None,
        "viewer_state": _viewer_state(
            is_starred=is_starred,
            my_reaction=my_reaction,
        ),
        "delivered_at": getattr(msg, "delivered_to_receiver_at", None),
        "read_at": getattr(msg, "read_by_receiver_at", None),
        "created_at": msg.creation,
    }


def _prepare_chat_attachments(
    *,
    attachments: List[Dict],
    current_user: str,
) -> tuple[List[Dict[str, Any]], Any | None]:
    """Validate chat attachment payloads and return normalized rows."""

    if not isinstance(attachments, list):
        return [], fail("attachments must be a list.", code="VALIDATION_ERROR")

    service = MediaService()
    prepared: List[Dict[str, Any]] = []

    for index, att in enumerate(attachments):
        if not isinstance(att, dict):
            return [], fail("Invalid attachment payload.", code="VALIDATION_ERROR")

        media_id = (
            att.get("media")
            or att.get("media_id")
            or att.get("id")
        )
        media_id = str(media_id or "").strip()

        if not media_id:
            if att.get("file"):
                return [], fail(
                    "Chat attachments must be uploaded as media_id using purpose chat_attachment.",
                    code="VALIDATION_ERROR",
                )
            return [], fail("Attachment media_id is required.", code="VALIDATION_ERROR")

        try:
            media_doc = service.assert_media_ready_for_attach(
                media_id=media_id,
                user=current_user,
                purpose="chat_attachment",
            )
        except MediaNotFoundError as exc:
            return [], fail(str(exc), code="NOT_FOUND")
        except MediaPermissionError as exc:
            return [], fail(str(exc), code="FORBIDDEN")
        except MediaValidationError as exc:
            return [], fail(str(exc), code="VALIDATION_ERROR")

        file_type = (
            att.get("file_type")
            or att.get("type")
            or _infer_attachment_type_from_content_type(media_doc.content_type)
        )
        file_type = str(file_type or "").strip().lower()

        if file_type not in {"image", "video", "audio", "document"}:
            file_type = _infer_attachment_type_from_content_type(media_doc.content_type)

        prepared.append(
            {
                "media": media_id,
                "file_type": file_type,
                "sort_order": index,
            }
        )

    return prepared, None


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
    short = kwargs.get("short")
    attachments = kwargs.get("attachments") or []
    reply_to_message = kwargs.get("reply_to_message")

    if not conv_id:
        return fail("conversation_id is required.", code="VALIDATION_ERROR")

    prepared_attachments, attachment_error = _prepare_chat_attachments(
        attachments=attachments,
        current_user=current_user,
    )
    if attachment_error:
        return attachment_error

    try:
        conv = _get_conversation_row(conv_id)
        if not conv:
            return fail("Conversation not found.", code="NOT_FOUND")

        if not _validate_sender(conv, current_user):
            return fail("Not allowed.", code="PERMISSION_DENIED")

        if not content and not attachments and not ad and not short:
            return fail(
                "Message must have content, attachments, an ad, or a short.",
                code="VALIDATION_ERROR",
            )

        ad_error = _validate_ad_reference(ad)
        if ad_error:
            return ad_error

        receiver = _get_receiver(conv, current_user)

        short_error = _validate_short_reference(
            short,
            viewer=current_user,
            recipients=[receiver],
        )
        if short_error:
            return short_error

        reply_error = _validate_reply_to_message(
            reply_to_message=reply_to_message,
            conversation_id=conv_id,
        )
        if reply_error:
            return reply_error

        block_err = ensure_not_blocked(
            current_user=current_user,
            target_user=receiver,
            action="message",
        )
        if block_err:
            return block_err

        message_type = _determine_message_type(
            content=content,
            attachments=attachments,
            ad=ad,
            short=short,
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

        if short:
            msg.short = short

        if reply_to_message:
            msg.reply_to_message = reply_to_message

        msg.insert(ignore_permissions=True)

        # Attachments.
        has_attachments = 0
        media_service = MediaService()

        for att in prepared_attachments:
            frappe.get_doc(
                {
                    "doctype": "AOS Message Attachment",
                    "message": msg.name,
                    "media": att["media"],
                    "file_type": att["file_type"],
                    "sort_order": att["sort_order"],
                }
            ).insert(ignore_permissions=True)

            media_service.attach_media(
                media_id=att["media"],
                user=current_user,
                purpose="chat_attachment",
                attached_doctype="AOS Message",
                attached_name=msg.name,
                attached_field="attachments",
            )

            has_attachments = 1

        if has_attachments:
            msg.db_set("has_attachments", 1, update_modified=False)
            msg.has_attachments = 1
        else:
            msg.has_attachments = 0

        # Normal sent messages are not forwarded.
        msg.is_forwarded = 0
        msg.forwarded_from_message = None
        msg.forwarded_from_conversation = None

        # Build rich maps for response/realtime/preview.
        attachments_map = _serialize_attachments_bulk([msg.name], current_user=current_user)

        reply_map = _fetch_reply_messages_bulk(
            [reply_to_message] if reply_to_message else []
        )

        user_ids = [current_user]

        for replied in reply_map.values():
            if replied.sender:
                user_ids.append(replied.sender)

        ad_ids = []

        if ad:
            ad_ids.append(ad)

        for replied in reply_map.values():
            if replied.ad and not _is_deleted_for_everyone(replied):
                ad_ids.append(replied.ad)

        short_ids = []
        if short:
            short_ids.append(short)

        for replied in reply_map.values():
            if getattr(replied, "short", None) and not _is_deleted_for_everyone(replied):
                short_ids.append(replied.short)

        user_map = _fetch_users(user_ids)
        ad_map = _fetch_ads_bulk(ad_ids)
        short_map = _fetch_shorts_bulk(short_ids, viewer=current_user)

        serialized = _serialize_message(
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

        preview = _message_preview(
            content=msg.content,
            has_attachments=has_attachments,
            ad=ad,
            short=short,
            ad_preview=ad_map.get(ad) if ad else None,
            short_preview=short_map.get(short) if short else None,
        )

        # Update conversation.
        # New messages are visible to both participants, so update both
        # participant-specific previews.
        set_conversation_preview_for_new_message(
            conversation_id=conv_id,
            sender=current_user,
            preview=preview,
            sent_at=now,
        )

        if current_user == conv.participant_1:
            unread_field = "unread_count_2"
        else:
            unread_field = "unread_count_1"

        frappe.db.sql(
            f"""
            UPDATE `tabAOS Conversation`
            SET
                is_active_1 = 1,
                is_active_2 = 1,
                {unread_field} = COALESCE({unread_field}, 0) + 1
            WHERE name = %s
            """,
            (conv_id,),
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

        # Refresh seller response metrics after this transaction commits.
        # Both participants are checked because either participant may own
        # an active seller profile. Non-sellers are ignored by the service.
        enqueue_conversation_response_metrics_refresh(
            participant_1=conv.participant_1,
            participant_2=conv.participant_2,
        )

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

        delete_condition = get_user_delete_sql_condition(conv, current_user)

        params: Dict[str, Any] = {
            "conversation_id": conv_id,
            "limit": limit,
        }

        before_condition = ""

        if before:
            before_creation = frappe.db.get_value(
                "AOS Message",
                before,
                "creation",
            )

            if not before_creation:
                return fail("Invalid 'before' message.", code="VALIDATION_ERROR")

            before_condition = "AND creation < %(before_creation)s"
            params["before_creation"] = before_creation

        messages = frappe.db.sql(
            f"""
            SELECT
                name,
                sender,
                content,
                message_type,
                ad,
                short,
                reply_to_message,
                has_attachments,
                is_forwarded,
                forwarded_from_message,
                forwarded_from_conversation,
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
                AND {delete_condition}
                {before_condition}
            ORDER BY creation DESC
            LIMIT %(limit)s
            """,
            params,
            as_dict=True,
        )

        if not messages:
            return ok("Messages fetched.", data=[])

        all_message_ids = [m.name for m in messages]
        visible_message_ids = [
            m.name for m in messages if not _is_deleted_for_everyone(m)
        ]

        starred_message_ids = _fetch_starred_message_ids(
            all_message_ids,
            current_user,
        )

        reaction_map = fetch_message_reaction_summaries(
            message_ids=visible_message_ids,
            viewer=current_user,
        )

        my_reaction_map = fetch_my_reactions(
            message_ids=visible_message_ids,
            user=current_user,
        )

        reply_message_ids = list(
            {
                m.reply_to_message
                for m in messages
                if getattr(m, "reply_to_message", None)
                and not _is_deleted_for_everyone(m)
            }
        )

        reply_map = _fetch_reply_messages_bulk(reply_message_ids)

        sender_ids = list({m.sender for m in messages if m.sender})

        for replied in reply_map.values():
            if replied.sender:
                sender_ids.append(replied.sender)

        sender_ids = list(set(sender_ids))

        ad_ids = list(
            {
                m.ad
                for m in messages
                if m.ad and not _is_deleted_for_everyone(m)
            }
        )

        for replied in reply_map.values():
            if replied.ad and not _is_deleted_for_everyone(replied):
                ad_ids.append(replied.ad)

        ad_ids = list(set(ad_ids))

        short_ids = list(
            {
                getattr(m, "short", None)
                for m in messages
                if getattr(m, "short", None) and not _is_deleted_for_everyone(m)
            }
        )

        for replied in reply_map.values():
            if getattr(replied, "short", None) and not _is_deleted_for_everyone(replied):
                short_ids.append(replied.short)

        short_ids = list(set(short_ids))

        attachments_map = _serialize_attachments_bulk(visible_message_ids, current_user=current_user)
        user_map = _fetch_users(sender_ids)
        ad_map = _fetch_ads_bulk(ad_ids)
        short_map = _fetch_shorts_bulk(short_ids, viewer=current_user)

        results = []

        for m in messages:
            results.append(
                _serialize_message(
                    m,
                    attachments_map=attachments_map,
                    user_map=user_map,
                    ad_map=ad_map,
                    short_map=short_map,
                    current_user=current_user,
                    reply_map=reply_map,
                    is_starred=m.name in starred_message_ids,
                    reactions=reaction_map.get(m.name, []),
                    my_reaction=my_reaction_map.get(m.name),
                )
            )

        return ok("Messages fetched.", data=results)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Messages Failed",
        )
        return fail("Failed to fetch messages.", code="INTERNAL_ERROR")
