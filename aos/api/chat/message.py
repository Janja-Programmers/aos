"""
Message APIs (implementation).

Handles:
- send_message
- list_messages
"""

from __future__ import annotations

import hashlib
import json

from typing import Any, Dict, List

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.blocking import ensure_not_blocked, get_blocked_user_set
from aos.api.shared.account_status import ensure_account_active
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.user_display import get_user_display_map
from aos.services.accounts.constants import ACCOUNT_STATUS_ACTIVE
from aos.services.live.errors import LiveError
from aos.services.live.policy import LivePolicy

from aos.services.notifications.service import NotificationService
from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
)
from aos.services.seller_response_metrics import (
    enqueue_conversation_response_metrics_refresh,
)
from aos.services.chat.events import publish_after_commit
from aos.services.chat.cursors import decode_cursor, encode_cursor
from aos.services.chat.errors import ChatError
from aos.services.chat.shared_objects import ad_is_shareable_to_users, fetch_chat_ad_previews

from .constants import (
    SEND_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
    LIST_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
)

from .presence import schedule_presence_update_to_peers
from .preview import set_conversation_preview_for_new_message
from .reactions import fetch_message_reaction_summaries, fetch_my_reactions
from .visibility import (
    get_deleted_for_everyone_display_text,
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


def _get_conversation_row(conv_id: str, *, lock: bool = False):
    query = """
        SELECT name, participant_1, participant_2
        FROM `tabAOS Conversation`
        WHERE name = %(conversation_id)s
        LIMIT 1
    """
    if lock:
        query += " FOR UPDATE"
    rows = frappe.db.sql(query, {"conversation_id": conv_id}, as_dict=True)
    return rows[0] if rows else None


def _validate_sender(conv, sender: str) -> bool:
    return sender in (conv.participant_1, conv.participant_2)


def _get_receiver(conv, sender: str) -> str:
    return (
        conv.participant_2
        if conv.participant_1 == sender
        else conv.participant_1
    )


def _fetch_users(users: List[str], *, viewer: str | None = None) -> Dict[str, dict]:
    """Fetch display-safe identities and suppress Live presence across blocks."""

    result = get_user_display_map(users)
    if not viewer or not result:
        return result
    blocked = get_blocked_user_set(viewer, result.keys())
    for internal_user in blocked:
        payload = result.get(internal_user)
        if not payload:
            continue
        payload["is_live"] = False
        payload["live_id"] = None
        payload["live_status"] = None
        payload["live_title"] = None
        payload["live_cover_image"] = None
        payload["live_cover_media"] = None
        payload["live_cover_media_id"] = None
        payload["live_started_at"] = None
        payload["live_viewer_count"] = 0
    return result


def _serialize_user(user_id: str, user_map: Dict[str, dict]) -> Dict[str, Any]:
    """Build display fields for a user."""

    user = user_map.get(user_id)

    return {
        "sender": user.get("user") if user else None,
        "sender_display_name": (
            user.get("display_name")
            if user
            else "AOS User"
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


def _infer_attachment_type_from_content_type(content_type: str | None) -> str:
    content_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    if content_type.startswith("audio/"):
        return "audio"
    return "document"


def _serialize_attachments_bulk(
    message_ids: List[str],
    *,
    current_user: str | None = None,
) -> Dict[str, List[Dict]]:
    """Project bounded attachment metadata through the hardened Media boundary."""

    unique_message_ids = list(dict.fromkeys(message_id for message_id in message_ids if message_id))
    if not unique_message_ids:
        return {}

    rows = frappe.get_all(
        "AOS Message Attachment",
        filters={"message": ["in", unique_message_ids]},
        fields=["message", "media", "sort_order"],
        order_by="message asc, sort_order asc, name asc",
        limit=max(1, len(unique_message_ids) * 10),
    )
    if not rows:
        return {}

    media_ids = list(dict.fromkeys(str(row.media) for row in rows if row.media))
    media_rows = frappe.get_all(
        "AOS Media Object",
        filters={"name": ["in", media_ids]},
        fields=[
            "name", "original_filename", "content_type", "size_bytes",
            "width", "height", "duration_seconds",
        ],
        limit=max(1, len(media_ids)),
    ) if media_ids else []
    media_map = {str(row.name): row for row in media_rows}

    urls: Dict[str, str] = {}
    if current_user and media_ids:
        try:
            urls = MediaService().get_chat_attachment_url_map(media_ids, user=current_user)
        except (MediaNotFoundError, MediaPermissionError, MediaValidationError):
            urls = {}
        except Exception:
            frappe.log_error("Chat attachment URL projection failed.", "AOS Chat Media Projection")
            urls = {}

    grouped: Dict[str, List[Dict]] = {}
    for row in rows:
        media_id = str(row.media or "").strip()
        media = media_map.get(media_id)
        url = urls.get(media_id)
        if not media or not url:
            continue
        grouped.setdefault(str(row.message), []).append(
            {
                "media_id": media_id,
                "url": url,
                "type": _infer_attachment_type_from_content_type(media.content_type),
                "sort_order": int(row.sort_order or 0),
                "filename": media.original_filename,
                "content_type": media.content_type,
                "size_bytes": media.size_bytes,
                "width": media.width,
                "height": media.height,
                "duration_seconds": media.duration_seconds,
            }
        )
    return grouped


def _fetch_ads_bulk(
    ad_ids: List[str],
    *,
    viewer: str | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Fetch privacy-safe active Ad previews in one bounded batch."""
    return fetch_chat_ad_previews(ad_ids, viewer=viewer)


def _ad_unavailable_payload(ad_id: str | None, ad_map: Dict[str, Dict[str, Any]]):
    if not ad_id:
        return {"ad_preview": None, "ad_unavailable": False}
    preview = ad_map.get(ad_id)
    return {"ad_preview": preview, "ad_unavailable": preview is None}

def _fetch_shorts_bulk(
    short_ids: List[str],
    *,
    viewer: str | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Fetch privacy-safe Short previews in one bounded batch.

    Chat stores only the Short reference.  Availability and creator identity are
    resolved from the canonical Shorts/Accounts/Media contracts at read time.
    """
    unique_short_ids = list(dict.fromkeys(str(value).strip() for value in short_ids if value))[:100]
    if not unique_short_ids:
        return {}

    rows = frappe.get_all(
        "AOS Short",
        filters={"name": ["in", unique_short_ids]},
        fields=[
            "name", "owner", "caption", "content_type",
            "poster_media", "cover_media", "playback_media",
            "duration_seconds", "lifecycle_status", "processing_status",
            "moderation_status", "audience", "like_count",
            "comment_count", "share_count", "repost_count",
        ],
        limit=len(unique_short_ids),
    )

    from aos.services.media.media_service import MediaService
    from aos.services.shorts.policy import filter_viewable_rows

    visible_rows = filter_viewable_rows([dict(row) for row in rows], viewer=viewer)
    owner_map = _fetch_users(
        [row.get("owner") for row in visible_rows if row.get("owner")],
        viewer=viewer,
    )
    media_ids = {
        str(row.get(field))
        for row in visible_rows
        for field in ("poster_media", "cover_media", "playback_media")
        if row.get(field)
    }
    media_urls = MediaService().get_public_url_map(media_ids)

    result: Dict[str, Dict[str, Any]] = {}
    for row in visible_rows:
        owner_id = row.get("owner")
        owner = owner_map.get(owner_id) or {}
        result[row.get("name")] = {
            "id": row.get("name"),
            "owner": owner.get("user"),
            "owner_account_id": owner.get("account_id") or owner.get("user"),
            "owner_display_name": owner.get("display_name"),
            "owner_avatar": owner.get("avatar"),
            "caption": row.get("caption") or "",
            "content_type": row.get("content_type"),
            "thumbnail_url": media_urls.get(str(row.get("cover_media") or row.get("poster_media") or "")),
            "playback_url": media_urls.get(str(row.get("playback_media") or "")),
            "duration_seconds": row.get("duration_seconds"),
            "like_count": row.get("like_count") or 0,
            "comment_count": row.get("comment_count") or 0,
            "share_count": row.get("share_count") or 0,
            "repost_count": row.get("repost_count") or 0,
        }

    return result


def _can_view_short(short_row, *, current_user: str | None = None) -> bool:
    from aos.services.shorts.policy import can_view
    return can_view(short_row, viewer=current_user)


def _get_short_reference(short: str | None):
    if not short:
        return None
    return frappe.db.get_value(
        "AOS Short",
        short,
        ["name", "owner", "lifecycle_status", "processing_status", "moderation_status", "audience"],
        as_dict=True,
    )


def _validate_short_reference(
    short: str | None,
    *,
    viewer: str | None = None,
    recipients: List[str] | None = None,
):
    """Ensure every conversation participant may still view the Short."""
    if not short:
        return None
    short_row = _get_short_reference(short)
    if not short_row or not _can_view_short(short_row, current_user=viewer):
        return fail("Short is not available.", error="VALIDATION_ERROR")
    for recipient in recipients or []:
        if not _can_view_short(short_row, current_user=recipient):
            return fail(
                "This short cannot be shared with one or more recipients.",
                error="FORBIDDEN",
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


def _fetch_lives_bulk(
    live_ids: List[str],
    *,
    viewer: str | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Fetch privacy-safe Live previews in one bounded batch.

    The preview intentionally omits room names, tokens, session identifiers,
    internal User values and moderation state. Ended Lives remain representable
    in chat history when the viewer still has access.
    """
    unique_ids = list({str(live_id).strip() for live_id in live_ids if live_id})
    if not unique_ids:
        return {}

    rows = frappe.db.sql(
        """
        SELECT l.name, l.host_user, l.title, l.cover_image, l.live_cover_media,
               l.status, l.is_active, l.viewer_count, l.started_at, l.ended_at,
               COALESCE(u.enabled, 0) AS host_enabled,
               COALESCE(NULLIF(p.account_status, ''), %(active)s) AS account_status,
               CASE WHEN p.account_status = 'Deleted' THEN 1 ELSE 0 END AS host_deleted
        FROM `tabAOS Live Stream` l
        LEFT JOIN `tabUser` u ON u.name = l.host_user
        LEFT JOIN `tabAOS Profile` p ON p.user = l.host_user
        WHERE l.name IN %(live_ids)s
        """,
        {"live_ids": tuple(unique_ids), "active": ACCOUNT_STATUS_ACTIVE},
        as_dict=True,
    )
    hosts = [str(row.host_user) for row in rows if row.host_user]
    blocked = get_blocked_user_set(viewer, hosts) if viewer else set()
    user_map = get_user_display_map(hosts)

    media_ids = [str(row.live_cover_media) for row in rows if row.live_cover_media]
    try:
        media_urls = MediaService().get_public_url_map(media_ids) if media_ids else {}
    except Exception:
        media_urls = {}

    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        host = str(row.host_user or "")
        if not host or host in blocked:
            continue
        if int(row.host_enabled or 0) != 1 or bool(int(row.host_deleted or 0)):
            continue
        if str(row.account_status or ACCOUNT_STATUS_ACTIVE) != ACCOUNT_STATUS_ACTIVE:
            continue
        display = user_map.get(host) or {}
        if bool(display.get("is_deleted")):
            continue
        cover = row.cover_image
        if not cover and row.live_cover_media:
            cover = media_urls.get(str(row.live_cover_media))
        result[str(row.name)] = {
            "id": str(row.name),
            "live_id": str(row.name),
            "title": str(row.title or "Live"),
            "cover_image": cover or None,
            "status": str(row.status or ""),
            "is_live": bool(row.is_active) and str(row.status or "") == "live",
            "viewer_count": max(0, int(row.viewer_count or 0)),
            "started_at": row.started_at,
            "ended_at": row.ended_at,
            "host": {
                "account_id": display.get("account_id") or display.get("account_id"),
                "user": display.get("account_id"),
                "display_name": display.get("display_name") or "AOS User",
                "avatar": display.get("avatar"),
            },
        }
    return result


def _live_unavailable_payload(live_id: str | None, live_map: Dict[str, Dict[str, Any]]):
    if not live_id:
        return {"live_preview": None, "live_unavailable": False}
    preview = live_map.get(live_id)
    return {"live_preview": preview, "live_unavailable": preview is None}


def _validate_live_reference(
    live: str | None,
    *,
    viewer: str | None = None,
    recipients: List[str] | None = None,
    require_active: bool = False,
):
    """Validate a native Live reference without exposing private Live state."""
    if not live:
        return None
    row = frappe.db.get_value(
        "AOS Live Stream",
        live,
        ["name", "host_user", "status", "is_active"],
        as_dict=True,
    )
    if not row:
        return fail("Live is not available.", error="NOT_FOUND", http_status=404)
    if require_active and (str(row.status or "") != "live" or not bool(row.is_active)):
        return fail("Live is not available.", error="NOT_FOUND", http_status=404)
    audience = [user for user in [viewer, *(recipients or [])] if user]
    policy = LivePolicy()
    try:
        for user in audience:
            policy.lock_relationship(host_user=str(row.host_user), viewer=user)
            policy.require_view_access(host_user=str(row.host_user), viewer=user)
            if not _fetch_lives_bulk([live], viewer=user).get(live):
                return fail("Live is not available.", error="NOT_FOUND", http_status=404)
    except LiveError:
        return fail("Live is not available.", error="NOT_FOUND", http_status=404)
    return None


def _message_idempotency_digest(*, sender: str, conversation_id: str, key: str | None, operation: str = "send") -> str | None:
    raw = str(key or "").strip()
    if not raw:
        return None
    material = "\x1f".join(["chat", operation, str(conversation_id), str(sender), raw])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _message_request_hash(
    *,
    content: str,
    attachments: List[Dict[str, Any]],
    ad: str | None,
    short: str | None,
    live: str | None,
    reply_to_message: str | None,
) -> str:
    canonical = {
        "content": content or "",
        "attachments": [str(row.get("media_id") or "") for row in attachments],
        "ad": str(ad or ""),
        "short": str(short or ""),
        "live": str(live or ""),
        "reply_to_message": str(reply_to_message or ""),
    }
    raw = json.dumps(canonical, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _get_idempotent_message(*, digest: str | None):
    if not digest:
        return None
    name = frappe.db.get_value("AOS Message", {"idempotency_key": digest}, "name")
    return frappe.get_doc("AOS Message", name) if name else None


def _idempotency_replay_response(existing, *, request_hash: str, current_user: str):
    if str(getattr(existing, "idempotency_request_hash", "") or "") != request_hash:
        return fail(
            "Idempotency key was already used for a different message.",
            error="CHAT_CONFLICT",
            http_status=409,
        )
    return ok(
        "Message already sent.",
        data=_serialize_existing_message_for_viewer(existing, current_user=current_user),
    )


def _serialize_existing_message_for_viewer(existing, *, current_user: str) -> Dict[str, Any]:
    reply_map = _fetch_reply_messages_bulk(
        [existing.reply_to_message] if getattr(existing, "reply_to_message", None) else []
    )
    user_ids = [existing.sender] + [row.sender for row in reply_map.values() if row.sender]
    ad_ids = [value for value in [getattr(existing, "ad", None)] if value]
    short_ids = [value for value in [getattr(existing, "short", None)] if value]
    live_ids = [value for value in [getattr(existing, "live", None)] if value]
    for row in reply_map.values():
        if _is_deleted_for_everyone(row):
            continue
        if getattr(row, "ad", None):
            ad_ids.append(row.ad)
        if getattr(row, "short", None):
            short_ids.append(row.short)
        if getattr(row, "live", None):
            live_ids.append(row.live)
    return _serialize_message(
        existing,
        attachments_map=_serialize_attachments_bulk([existing.name], current_user=current_user),
        user_map=_fetch_users(user_ids, viewer=current_user),
        ad_map=_fetch_ads_bulk(ad_ids, viewer=current_user),
        short_map=_fetch_shorts_bulk(short_ids, viewer=current_user),
        live_map=_fetch_lives_bulk(live_ids, viewer=current_user),
        current_user=current_user,
        reply_map=reply_map,
        is_starred=False,
        reactions=[],
        my_reaction=None,
    )


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
            "live",
            "call_id",
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
        limit=max(1, len(unique_ids)),
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
        limit=max(1, len(unique_message_ids)),
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


def _validate_ad_reference(
    ad: str | None,
    *,
    viewer: str | None = None,
    recipients: List[str] | None = None,
):
    """Validate an Ad through the same active/block-aware marketplace policy."""
    if not ad:
        return None
    audience = [user for user in [viewer, *(recipients or [])] if user]
    if not ad_is_shareable_to_users(ad, users=audience):
        # Deliberately non-enumerating for inactive, expired, blocked or missing Ads.
        return fail("Ad is not available.", error="NOT_FOUND", http_status=404)
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
        return fail("Reply message not found.", error="NOT_FOUND")

    if replied.conversation != conversation_id:
        return fail(
            "You can only reply to a message in the same conversation.",
            error="VALIDATION_ERROR",
        )

    return None


def _determine_message_type(
    *,
    content: str,
    attachments: List[Dict],
    ad: str | None,
    short: str | None = None,
    live: str | None = None,
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

    if live:
        parts += 1

    if parts > 1:
        return "mixed"

    if ad:
        return "ad"

    if short:
        return "short"

    if live:
        return "live"

    if attachments:
        return "media"

    return "text"


def _message_preview(
    *,
    content: str | None,
    has_attachments: int,
    ad: str | None,
    short: str | None = None,
    live: str | None = None,
    ad_preview: Dict[str, Any] | None = None,
    short_preview: Dict[str, Any] | None = None,
    live_preview: Dict[str, Any] | None = None,
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

    if live:
        title = live_preview.get("title") if live_preview else None
        return title or "[Live]"

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
        "ad_unavailable": False,
        "short": None,
        "short_preview": None,
        "short_unavailable": False,
        "live": None,
        "live_preview": None,
        "live_unavailable": False,
        "call_id": None,
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
    live_map: Dict[str, Dict[str, Any]] | None = None,
    current_user: str = "",
) -> Dict[str, Any] | None:
    """
    Build lightweight reply preview for frontend rendering.
    """

    if not reply_to_message:
        return None

    short_map = short_map or {}
    live_map = live_map or {}

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
            "ad_unavailable": False,
            "short": None,
            "short_preview": None,
            "short_unavailable": False,
            "live": None,
            "live_preview": None,
            "live_unavailable": False,
            "call_id": None,
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
    ad_payload = _ad_unavailable_payload(getattr(replied, "ad", None), ad_map)
    short_payload = _short_unavailable_payload(short_id, short_map)
    live_id = getattr(replied, "live", None)
    live_payload = _live_unavailable_payload(live_id, live_map)

    return {
        "id": replied.name,
        "sender": replied_user["sender"],
        "sender_display_name": replied_user["sender_display_name"],
        "sender_avatar": replied_user["sender_avatar"],
        "content": replied.content,
        "message_type": replied.message_type,
        "ad": replied.ad,
        "ad_preview": ad_payload["ad_preview"],
        "ad_unavailable": ad_payload["ad_unavailable"],
        "short": short_id,
        "short_preview": short_payload["short_preview"],
        "short_unavailable": short_payload["short_unavailable"],
        "live": live_id,
        "live_preview": live_payload["live_preview"],
        "live_unavailable": live_payload["live_unavailable"],
        "call_id": getattr(replied, "call_id", None),
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
    live_map: Dict[str, Dict[str, Any]] | None = None,
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
    live_map = live_map or {}

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
        live_map=live_map,
        current_user=current_user,
    )

    short_id = getattr(msg, "short", None)
    ad_payload = _ad_unavailable_payload(getattr(msg, "ad", None), ad_map)
    short_payload = _short_unavailable_payload(short_id, short_map)
    live_id = getattr(msg, "live", None)
    live_payload = _live_unavailable_payload(live_id, live_map)

    return {
        "id": msg.name,
        "sender": user_payload["sender"],
        "sender_display_name": user_payload["sender_display_name"],
        "sender_avatar": user_payload["sender_avatar"],
        "content": msg.content,
        "message_type": msg.message_type,
        "ad": msg.ad,
        "ad_preview": ad_payload["ad_preview"],
        "ad_unavailable": ad_payload["ad_unavailable"],
        "short": short_id,
        "short_preview": short_payload["short_preview"],
        "short_unavailable": short_payload["short_unavailable"],
        "live": live_id,
        "live_preview": live_payload["live_preview"],
        "live_unavailable": live_payload["live_unavailable"],
        "call_id": getattr(msg, "call_id", None),
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
    """Validate canonical ``{media_id}`` references through Media."""

    if not isinstance(attachments, list):
        return [], fail("attachments must be a list.", error="CHAT_INVALID_REQUEST")

    service = MediaService()
    prepared: List[Dict[str, Any]] = []
    for index, attachment in enumerate(attachments):
        if not isinstance(attachment, dict):
            return [], fail("Invalid attachment payload.", error="CHAT_INVALID_REQUEST")
        media_id = str(attachment.get("media_id") or "").strip()
        if not media_id:
            return [], fail("Attachment media_id is required.", error="CHAT_INVALID_REQUEST")
        try:
            service.assert_media_ready_for_attach(
                media_id=media_id,
                user=current_user,
                purpose="chat_attachment",
            )
        except MediaNotFoundError as exc:
            return [], safe_fail_from_exception(exc, fallback="Resource not found.", error="CHAT_NOT_FOUND")
        except MediaPermissionError as exc:
            return [], safe_fail_from_exception(exc, fallback="Not allowed.", error="CHAT_ACCESS_DENIED")
        except MediaValidationError as exc:
            return [], safe_fail_from_exception(exc, fallback="Invalid request.", error="CHAT_INVALID_REQUEST")
        prepared.append({"media_id": media_id, "sort_order": index})
    return prepared, None


# Canonical mutation core used by the public Chat endpoint and feature-owned
# share adapters (Shorts/Live). Authentication and feature-specific rate limits
# remain at their public boundaries; this function owns Chat authorization,
# references, idempotency, persistence, notification/outbox and realtime state.
def send_message_for_user(*, current_user: str, require_live_active: bool = False, **kwargs):
    conv_id = kwargs.get("conversation_id")
    content = (kwargs.get("content") or "").strip()
    ad = kwargs.get("ad")
    short = kwargs.get("short")
    live = kwargs.get("live")
    attachments = kwargs.get("attachments") or []
    reply_to_message = kwargs.get("reply_to_message")
    client_idempotency_key = kwargs.get("idempotency_key")

    if not conv_id:
        return fail("conversation_id is required.", error="VALIDATION_ERROR")

    prepared_attachments, attachment_error = _prepare_chat_attachments(
        attachments=attachments,
        current_user=current_user,
    )
    if attachment_error:
        return attachment_error

    try:
        conv = _get_conversation_row(conv_id, lock=True)
        if not conv:
            return fail("Conversation not found.", error="NOT_FOUND")

        if not _validate_sender(conv, current_user):
            return fail("Not allowed.", error="PERMISSION_DENIED")

        if not content and not attachments and not ad and not short and not live:
            return fail(
                "Message must have content, attachments, an ad, a short, or a live.",
                error="VALIDATION_ERROR",
            )

        reference_count = sum(bool(value) for value in (ad, short, live))
        if reference_count > 1:
            return fail(
                "A message can reference only one shared object.",
                error="VALIDATION_ERROR",
            )

        receiver = _get_receiver(conv, current_user)
        if not frappe.db.exists("User", {"name": receiver, "enabled": 1}) or ensure_account_active(receiver):
            return fail("Recipient is unavailable.", error="NOT_FOUND", http_status=404)

        ad_error = _validate_ad_reference(
            ad,
            viewer=current_user,
            recipients=[receiver],
        )
        if ad_error:
            return ad_error

        short_error = _validate_short_reference(
            short,
            viewer=current_user,
            recipients=[receiver],
        )
        if short_error:
            return short_error

        live_error = _validate_live_reference(
            live,
            viewer=current_user,
            recipients=[receiver],
            require_active=require_live_active,
        )
        if live_error:
            return live_error

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

        idempotency_digest = _message_idempotency_digest(
            sender=current_user,
            conversation_id=conv_id,
            key=client_idempotency_key,
        )
        request_hash = _message_request_hash(
            content=content,
            attachments=prepared_attachments,
            ad=ad,
            short=short,
            live=live,
            reply_to_message=reply_to_message,
        )
        existing = _get_idempotent_message(digest=idempotency_digest)
        if existing:
            return _idempotency_replay_response(
                existing, request_hash=request_hash, current_user=current_user
            )

        message_type = _determine_message_type(
            content=content,
            attachments=prepared_attachments,
            ad=ad,
            short=short,
            live=live,
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

        if live:
            msg.live = live

        if idempotency_digest:
            msg.idempotency_key = idempotency_digest
            msg.idempotency_request_hash = request_hash

        if reply_to_message:
            msg.reply_to_message = reply_to_message

        try:
            msg.insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            existing = _get_idempotent_message(digest=idempotency_digest)
            if not existing:
                raise
            return _idempotency_replay_response(
                existing, request_hash=request_hash, current_user=current_user
            )

        # Attachments.
        has_attachments = 0
        media_service = MediaService()

        for att in prepared_attachments:
            frappe.get_doc(
                {
                    "doctype": "AOS Message Attachment",
                    "message": msg.name,
                    "media": att["media_id"],
                    "sort_order": att["sort_order"],
                }
            ).insert(ignore_permissions=True)

            media_service.attach_media(
                media_id=att["media_id"],
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

        live_ids = []
        if live:
            live_ids.append(live)
        for replied in reply_map.values():
            if getattr(replied, "live", None) and not _is_deleted_for_everyone(replied):
                live_ids.append(replied.live)

        user_map = _fetch_users(user_ids, viewer=current_user)
        ad_map = _fetch_ads_bulk(ad_ids, viewer=current_user)
        short_map = _fetch_shorts_bulk(short_ids, viewer=current_user)
        live_map = _fetch_lives_bulk(live_ids, viewer=current_user)

        serialized = _serialize_message(
            msg,
            attachments_map=attachments_map,
            user_map=user_map,
            ad_map=ad_map,
            short_map=short_map,
            live_map=live_map,
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
            live=live,
            ad_preview=ad_map.get(ad) if ad else None,
            short_preview=short_map.get(short) if short else None,
            live_preview=live_map.get(live) if live else None,
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
            frappe.db.sql(
                """
                UPDATE `tabAOS Conversation`
                SET is_active_1 = 1, is_active_2 = 1,
                    unread_count_2 = COALESCE(unread_count_2, 0) + 1
                WHERE name = %s
                """,
                (conv_id,),
            )
        else:
            frappe.db.sql(
                """
                UPDATE `tabAOS Conversation`
                SET is_active_1 = 1, is_active_2 = 1,
                    unread_count_1 = COALESCE(unread_count_1, 0) + 1
                WHERE name = %s
                """,
                (conv_id,),
            )

        # Realtime.
        realtime_payload = {
            "conversation_id": conv_id,
            "message": serialized,
        }

        publish_after_commit(
            event="aos_new_message",
            message=realtime_payload,
            user=receiver,
        )

        # Persist notification/outbox in the same transaction when possible.
        # Notification failure is isolated from the committed Chat mutation.
        try:
            NotificationService.notify_new_message(
                user=receiver,
                sender=current_user,
                conversation_id=conv_id,
                preview=preview,
                message_id=msg.name,
            )
        except Exception:
            frappe.log_error("Chat notification creation failed.", "AOS Chat notification failure")

        # Presence activity is persisted in this transaction; realtime emits after commit.
        schedule_presence_update_to_peers(current_user)

        # Refresh seller response metrics after this transaction commits.
        # Both participants are checked because either participant may own
        # an active seller profile. Non-sellers are ignored by the service.
        enqueue_conversation_response_metrics_refresh(
            participant_1=conv.participant_1,
            participant_2=conv.participant_2,
        )

        return ok("Message sent.", data=serialized)

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error("Chat operation failed.", "AOS Send Message Failed")
        return fail("Failed to send message.", error="INTERNAL_ERROR")


# send_message
def send_message_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "send_message", current_user),
        ttl_seconds=60,
        limit=SEND_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many messages. Please slow down.",
    )
    if rl:
        return rl

    return send_message_for_user(
        current_user=current_user,
        # New native Live messages may only reference an active Live. Historical
        # Chat rows remain serializable after the Live ends.
        require_live_active=True,
        **kwargs,
    )


# list_messages
def list_messages_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "list_messages", current_user),
        ttl_seconds=60,
        limit=LIST_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")
    limit = _clean_int(kwargs.get("limit"), default=30, min_value=1, max_value=100)
    if not conv_id:
        return fail("conversation_id is required.", error="CHAT_INVALID_REQUEST")

    try:
        cursor = decode_cursor(
            kwargs.get("cursor"),
            kind="messages",
            required_keys=("created_at", "message_id"),
        )
    except ChatError as exc:
        return fail(exc.public_message, error=exc.code, data=exc.data, http_status=exc.http_status)

    try:
        conv = _get_conversation_row(conv_id)
        if not conv:
            return fail("Conversation not found.", error="CHAT_NOT_FOUND", http_status=404)
        if current_user not in (conv.participant_1, conv.participant_2):
            return fail("Not allowed.", error="CHAT_ACCESS_DENIED", http_status=403)

        participant_index = 1 if current_user == conv.participant_1 else 2
        params: Dict[str, Any] = {
            "conversation_id": conv_id,
            "participant_index": participant_index,
            "cursor_creation": cursor.get("created_at") if cursor else None,
            "cursor_name": cursor.get("message_id") if cursor else None,
            "query_limit": limit + 1,
        }

        messages = frappe.db.sql(
            """
            SELECT
                name, sender, content, message_type, ad, short, live, call_id, reply_to_message,
                has_attachments, is_forwarded, forwarded_from_message,
                forwarded_from_conversation, is_edited, edited_at,
                deleted_for_everyone, deleted_for_everyone_at,
                deleted_for_1, deleted_for_1_at, deleted_for_2, deleted_for_2_at,
                delivered_to_receiver_at, read_by_receiver_at, creation
            FROM `tabAOS Message`
            WHERE conversation = %(conversation_id)s
              AND (
                    (%(participant_index)s = 1 AND IFNULL(deleted_for_1, 0) = 0)
                 OR (%(participant_index)s = 2 AND IFNULL(deleted_for_2, 0) = 0)
              )
              AND (
                    %(cursor_creation)s IS NULL
                 OR creation < %(cursor_creation)s
                 OR (creation = %(cursor_creation)s AND name < %(cursor_name)s)
              )
            ORDER BY creation DESC, name DESC
            LIMIT %(query_limit)s
            """,
            params,
            as_dict=True,
        )

        has_more = len(messages) > limit
        page = list(messages[:limit])
        if not page:
            return ok("Messages fetched.", data={"items": [], "next_cursor": None})

        all_message_ids = [m.name for m in page]
        visible_message_ids = [m.name for m in page if not _is_deleted_for_everyone(m)]
        starred_message_ids = _fetch_starred_message_ids(all_message_ids, current_user)
        reaction_map = fetch_message_reaction_summaries(
            message_ids=visible_message_ids, viewer=current_user
        )
        my_reaction_map = fetch_my_reactions(
            message_ids=visible_message_ids, user=current_user
        )
        reply_message_ids = list({
            m.reply_to_message
            for m in page
            if getattr(m, "reply_to_message", None) and not _is_deleted_for_everyone(m)
        })
        reply_map = _fetch_reply_messages_bulk(reply_message_ids)

        sender_ids = list({m.sender for m in page if m.sender})
        sender_ids.extend(row.sender for row in reply_map.values() if row.sender)
        sender_ids = list(set(sender_ids))

        ad_ids = list({m.ad for m in page if m.ad and not _is_deleted_for_everyone(m)})
        ad_ids.extend(
            row.ad for row in reply_map.values()
            if getattr(row, "ad", None) and not _is_deleted_for_everyone(row)
        )
        ad_ids = list(set(ad_ids))

        short_ids = list({
            getattr(m, "short", None) for m in page
            if getattr(m, "short", None) and not _is_deleted_for_everyone(m)
        })
        short_ids.extend(
            row.short for row in reply_map.values()
            if getattr(row, "short", None) and not _is_deleted_for_everyone(row)
        )
        short_ids = list(set(short_ids))

        live_ids = list({
            getattr(m, "live", None) for m in page
            if getattr(m, "live", None) and not _is_deleted_for_everyone(m)
        })
        live_ids.extend(
            row.live for row in reply_map.values()
            if getattr(row, "live", None) and not _is_deleted_for_everyone(row)
        )
        live_ids = list(set(live_ids))

        attachments_map = _serialize_attachments_bulk(visible_message_ids, current_user=current_user)
        user_map = _fetch_users(sender_ids, viewer=current_user)
        ad_map = _fetch_ads_bulk(ad_ids, viewer=current_user)
        short_map = _fetch_shorts_bulk(short_ids, viewer=current_user)
        live_map = _fetch_lives_bulk(live_ids, viewer=current_user)

        results = [
            _serialize_message(
                message,
                attachments_map=attachments_map,
                user_map=user_map,
                ad_map=ad_map,
                short_map=short_map,
                live_map=live_map,
                current_user=current_user,
                reply_map=reply_map,
                is_starred=message.name in starred_message_ids,
                reactions=reaction_map.get(message.name, []),
                my_reaction=my_reaction_map.get(message.name),
            )
            for message in page
        ]
        last = page[-1]
        next_cursor = (
            encode_cursor(
                "messages",
                {"created_at": str(last.creation), "message_id": str(last.name)},
            )
            if has_more
            else None
        )
        return ok("Messages fetched.", data={"items": results, "next_cursor": next_cursor})

    except Exception:
        frappe.log_error("Chat operation failed.", "AOS List Messages Failed")
        return fail("Failed to fetch messages.", error="CHAT_INTERNAL_ERROR", http_status=500)

