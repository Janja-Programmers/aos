"""Bounded privacy-safe projections for Chat responses."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import frappe

from aos.api.shared.blocking import get_blocked_user_set
from aos.api.shared.user_display import get_user_display_map
from aos.services.accounts.constants import ACCOUNT_STATUS_ACTIVE
from aos.services.chat.shared_objects import fetch_chat_ad_previews
from aos.services.live.policy import LiveError, LivePolicy
from aos.services.media.media_service import MediaNotFoundError, MediaPermissionError, MediaService, MediaValidationError

DELETED_MESSAGE_TEXT = "This message was deleted"
YOU_DELETED_MESSAGE_TEXT = "You deleted this message"


def fetch_users(users: Iterable[str], *, viewer: str | None = None) -> dict[str, dict[str, Any]]:
    result = get_user_display_map(users)
    if not viewer or not result:
        return result
    blocked = get_blocked_user_set(viewer, result.keys())
    for user in blocked:
        payload = result.get(user)
        if not payload:
            continue
        for key, value in {
            "is_live": False,
            "live_id": None,
            "live_status": None,
            "live_title": None,
            "live_cover_image": None,
            "live_cover_media": None,
            "live_cover_media_id": None,
            "live_started_at": None,
            "live_viewer_count": 0,
        }.items():
            payload[key] = value
    return result


def sender_payload(user: str, user_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = user_map.get(user) or {}
    return {
        "sender": item.get("account_id"),
        "sender_display_name": item.get("display_name") or "AOS User",
        "sender_avatar": item.get("avatar"),
        "sender_is_deleted": bool(item.get("is_deleted")),
        "sender_is_live": bool(item.get("is_live")) if not item.get("is_deleted") else False,
        "sender_live_id": item.get("live_id") if not item.get("is_deleted") else None,
        "sender_live_status": item.get("live_status") if not item.get("is_deleted") else None,
    }


def _attachment_type(content_type: str | None) -> str:
    value = str(content_type or "").split(";", 1)[0].strip().lower()
    if value.startswith("image/"):
        return "image"
    if value.startswith("video/"):
        return "video"
    if value.startswith("audio/"):
        return "audio"
    return "document"


def fetch_attachments(message_ids: Iterable[str], *, viewer: str) -> dict[str, list[dict[str, Any]]]:
    ids = list(dict.fromkeys(str(v) for v in message_ids if v))[:100]
    if not ids:
        return {}
    rows = frappe.get_all(
        "AOS Message Attachment",
        filters={"message": ["in", ids]},
        fields=["message", "media", "sort_order"],
        order_by="message asc, sort_order asc, name asc",
        limit=max(1, len(ids) * 10),
    )
    media_ids = list(dict.fromkeys(str(row.media) for row in rows if row.media))
    media_rows = (
        frappe.get_all(
            "AOS Media Object",
            filters={"name": ["in", media_ids]},
            fields=["name", "original_filename", "content_type", "size_bytes", "width", "height", "duration_seconds"],
            limit=max(1, len(media_ids)),
        )
        if media_ids
        else []
    )
    media_map = {str(row.name): row for row in media_rows}
    try:
        urls = MediaService().get_chat_attachment_url_map(media_ids, user=viewer) if media_ids else {}
    except (MediaNotFoundError, MediaPermissionError, MediaValidationError):
        urls = {}
    except Exception:
        frappe.log_error("Chat attachment URL projection failed.", "AOS Chat Media Projection")
        urls = {}
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        media_id = str(row.media or "")
        media = media_map.get(media_id)
        url = urls.get(media_id)
        if not media or not url:
            continue
        result.setdefault(str(row.message), []).append(
            {
                "media_id": media_id,
                "url": url,
                "type": _attachment_type(media.content_type),
                "sort_order": int(row.sort_order or 0),
                "filename": media.original_filename,
                "content_type": media.content_type,
                "size_bytes": media.size_bytes,
                "width": media.width,
                "height": media.height,
                "duration_seconds": media.duration_seconds,
            }
        )
    return result


def fetch_ads(ids: Iterable[str], *, viewer: str) -> dict[str, dict[str, Any]]:
    return fetch_chat_ad_previews(ids, viewer=viewer)


def fetch_shorts(ids: Iterable[str], *, viewer: str) -> dict[str, dict[str, Any]]:
    unique = list(dict.fromkeys(str(v).strip() for v in ids if v))[:100]
    if not unique:
        return {}
    rows = frappe.get_all(
        "AOS Short",
        filters={"name": ["in", unique]},
        fields=[
            "name", "owner", "caption", "content_type", "poster_media", "cover_media", "playback_media",
            "duration_seconds", "lifecycle_status", "processing_status", "moderation_status", "audience",
            "like_count", "comment_count", "share_count", "repost_count",
        ],
        limit=len(unique),
    )
    from aos.services.shorts.policy import filter_viewable_rows

    visible = filter_viewable_rows([dict(row) for row in rows], viewer=viewer)
    owners = fetch_users([row.get("owner") for row in visible if row.get("owner")], viewer=viewer)
    media_ids = {
        str(row.get(field))
        for row in visible
        for field in ("poster_media", "cover_media", "playback_media")
        if row.get(field)
    }
    urls = MediaService().get_public_url_map(media_ids)
    result: dict[str, dict[str, Any]] = {}
    for row in visible:
        owner = owners.get(str(row.get("owner"))) or {}
        result[str(row.get("name"))] = {
            "id": row.get("name"),
            "owner_account_id": owner.get("account_id"),
            "owner_display_name": owner.get("display_name"),
            "owner_avatar": owner.get("avatar"),
            "caption": row.get("caption") or "",
            "content_type": row.get("content_type"),
            "thumbnail_url": urls.get(str(row.get("cover_media") or row.get("poster_media") or "")),
            "playback_url": urls.get(str(row.get("playback_media") or "")),
            "duration_seconds": row.get("duration_seconds"),
            "like_count": int(row.get("like_count") or 0),
            "comment_count": int(row.get("comment_count") or 0),
            "share_count": int(row.get("share_count") or 0),
            "repost_count": int(row.get("repost_count") or 0),
        }
    return result


def short_shareable(short_id: str, *, users: Iterable[str]) -> bool:
    row = frappe.db.get_value(
        "AOS Short",
        short_id,
        ["name", "owner", "lifecycle_status", "processing_status", "moderation_status", "audience"],
        as_dict=True,
    )
    if not row:
        return False
    from aos.services.shorts.policy import can_view

    return all(can_view(row, viewer=user) for user in users)


def fetch_lives(ids: Iterable[str], *, viewer: str) -> dict[str, dict[str, Any]]:
    unique = list(dict.fromkeys(str(v).strip() for v in ids if v))[:100]
    if not unique:
        return {}
    rows = frappe.db.sql(
        """
        SELECT l.name, l.host_user, l.title, l.cover_image, l.live_cover_media,
               l.status, l.is_active, l.viewer_count, l.started_at, l.ended_at,
               COALESCE(u.enabled,0) AS host_enabled,
               COALESCE(NULLIF(p.account_status,''), %(active)s) AS account_status,
               CASE WHEN p.account_status='Deleted' THEN 1 ELSE 0 END AS host_deleted
        FROM `tabAOS Live Stream` l
        LEFT JOIN `tabUser` u ON u.name=l.host_user
        LEFT JOIN `tabAOS Profile` p ON p.user=l.host_user
        WHERE l.name IN %(ids)s
        """,
        {"ids": tuple(unique), "active": ACCOUNT_STATUS_ACTIVE},
        as_dict=True,
    )
    hosts = [str(row.host_user) for row in rows if row.host_user]
    blocked = get_blocked_user_set(viewer, hosts)
    users = fetch_users(hosts, viewer=viewer)
    media_ids = [str(row.live_cover_media) for row in rows if row.live_cover_media]
    try:
        urls = MediaService().get_public_url_map(media_ids) if media_ids else {}
    except Exception:
        urls = {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        host = str(row.host_user or "")
        if not host or host in blocked or int(row.host_enabled or 0) != 1 or int(row.host_deleted or 0):
            continue
        if str(row.account_status or ACCOUNT_STATUS_ACTIVE) != ACCOUNT_STATUS_ACTIVE:
            continue
        display = users.get(host) or {}
        if display.get("is_deleted"):
            continue
        cover = row.cover_image or urls.get(str(row.live_cover_media or ""))
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
                "account_id": display.get("account_id"),
                "display_name": display.get("display_name") or "AOS User",
                "avatar": display.get("avatar"),
            },
        }
    return result


def live_shareable(live_id: str, *, users: Iterable[str], require_active: bool = False) -> bool:
    row = frappe.db.get_value("AOS Live Stream", live_id, ["name", "host_user", "status", "is_active"], as_dict=True)
    if not row:
        return False
    if require_active and (str(row.status or "") != "live" or not bool(row.is_active)):
        return False
    policy = LivePolicy()
    try:
        for user in users:
            policy.lock_relationship(host_user=str(row.host_user), viewer=user)
            policy.require_view_access(host_user=str(row.host_user), viewer=user)
            if live_id not in fetch_lives([live_id], viewer=user):
                return False
    except LiveError:
        return False
    return True


def message_preview(*, content: str | None, has_attachments: bool, ad: str | None, short: str | None, live: str | None,
                    ad_preview: dict[str, Any] | None = None, short_preview: dict[str, Any] | None = None,
                    live_preview: dict[str, Any] | None = None) -> str:
    if content:
        return str(content)
    if ad:
        return str((ad_preview or {}).get("title") or "[Ad]")
    if short:
        return str((short_preview or {}).get("caption") or "[Short]")
    if live:
        return str((live_preview or {}).get("title") or "[Live]")
    if has_attachments:
        return "[Attachment]"
    return "[Message]"


def serialize_messages(
    rows: list[Any],
    *,
    viewer: str,
    conversation_type: str,
    peer_user: str | None = None,
    reactions_map: dict[str, list[dict[str, Any]]] | None = None,
    my_reactions: dict[str, str] | None = None,
    starred: set[str] | None = None,
    receipt_map: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    ids = [str(row.name) for row in rows]
    reply_ids = [str(row.reply_to_message) for row in rows if row.reply_to_message]
    reply_rows = (
        frappe.get_all(
            "AOS Message",
            filters={"name": ["in", list(dict.fromkeys(reply_ids))]},
            fields=["name", "sender", "content", "message_type", "ad", "short", "live", "call_id", "has_attachments", "is_forwarded", "is_edited", "edited_at", "deleted_for_everyone", "deleted_for_everyone_at", "creation"],
            limit=max(1, len(set(reply_ids))),
        )
        if reply_ids
        else []
    )
    reply_map = {str(row.name): row for row in reply_rows}
    all_rows = [*rows, *reply_rows]
    users = fetch_users([str(row.sender) for row in all_rows if row.sender and row.sender != "Administrator"], viewer=viewer)
    attachments = fetch_attachments(ids, viewer=viewer)
    ads = fetch_ads([str(row.ad) for row in all_rows if row.ad], viewer=viewer)
    shorts = fetch_shorts([str(row.short) for row in all_rows if row.short], viewer=viewer)
    lives = fetch_lives([str(row.live) for row in all_rows if row.live], viewer=viewer)
    reactions_map = reactions_map or {}
    my_reactions = my_reactions or {}
    starred = starred or set()
    receipt_map = receipt_map or {}

    def ref_payload(row):
        ad_id = str(row.ad or "") or None
        short_id = str(row.short or "") or None
        live_id = str(row.live or "") or None
        return {
            "ad": ad_id,
            "ad_preview": ads.get(ad_id) if ad_id else None,
            "ad_unavailable": bool(ad_id and ad_id not in ads),
            "short": short_id,
            "short_preview": shorts.get(short_id) if short_id else None,
            "short_unavailable": bool(short_id and short_id not in shorts),
            "live": live_id,
            "live_preview": lives.get(live_id) if live_id else None,
            "live_unavailable": bool(live_id and live_id not in lives),
        }

    def reply_payload(row):
        if not row.reply_to_message:
            return None
        replied = reply_map.get(str(row.reply_to_message))
        if not replied:
            return None
        sender = sender_payload(str(replied.sender), users) if replied.sender != "Administrator" else {
            "sender": None, "sender_display_name": "AOS", "sender_avatar": None
        }
        if int(replied.deleted_for_everyone or 0):
            return {
                "id": replied.name,
                **sender,
                "content": None,
                "message_type": "deleted",
                "original_message_type": replied.message_type,
                "display_text": YOU_DELETED_MESSAGE_TEXT if replied.sender == viewer else DELETED_MESSAGE_TEXT,
                "created_at": replied.creation,
            }
        return {
            "id": replied.name,
            **sender,
            "content": replied.content,
            "message_type": replied.message_type,
            **ref_payload(replied),
            "call_id": replied.call_id,
            "has_attachments": int(replied.has_attachments or 0),
            "is_forwarded": int(replied.is_forwarded or 0),
            "is_edited": int(replied.is_edited or 0),
            "edited_at": replied.edited_at,
            "created_at": replied.creation,
        }

    result: list[dict[str, Any]] = []
    for row in rows:
        sender = sender_payload(str(row.sender), users) if row.sender != "Administrator" else {
            "sender": None,
            "sender_display_name": "AOS",
            "sender_avatar": None,
            "sender_is_deleted": False,
            "sender_is_live": False,
            "sender_live_id": None,
            "sender_live_status": None,
        }
        receipt = receipt_map.get(str(row.name)) or {}
        base = {
            "id": row.name,
            "conversation_id": row.conversation,
            **sender,
            "reply_to_message": row.reply_to_message,
            "reply_to": reply_payload(row),
            "is_forwarded": int(row.is_forwarded or 0),
            "is_edited": int(row.is_edited or 0),
            "edited_at": row.edited_at,
            "viewer_state": {"is_starred": str(row.name) in starred, "my_reaction": my_reactions.get(str(row.name))},
            "created_at": row.creation,
        }
        if int(row.deleted_for_everyone or 0):
            result.append({
                **base,
                "content": None,
                "message_type": "deleted",
                "original_message_type": row.message_type,
                "ad": None, "ad_preview": None, "ad_unavailable": False,
                "short": None, "short_preview": None, "short_unavailable": False,
                "live": None, "live_preview": None, "live_unavailable": False,
                "call_id": None,
                "has_attachments": 0,
                "attachments": [],
                "reactions": [],
                "is_deleted_for_everyone": 1,
                "deleted_for_everyone_at": row.deleted_for_everyone_at,
                "display_text": YOU_DELETED_MESSAGE_TEXT if row.sender == viewer else DELETED_MESSAGE_TEXT,
                "delivered_at": receipt.get("delivered_at"),
                "read_at": receipt.get("read_at"),
                "delivery_summary": receipt.get("summary"),
            })
            continue
        result.append({
            **base,
            "content": row.content,
            "message_type": row.message_type,
            **ref_payload(row),
            "call_id": row.call_id,
            "has_attachments": int(row.has_attachments or 0),
            "attachments": attachments.get(str(row.name), []),
            "reactions": reactions_map.get(str(row.name), []),
            "is_deleted_for_everyone": 0,
            "deleted_for_everyone_at": None,
            "display_text": None,
            "delivered_at": receipt.get("delivered_at"),
            "read_at": receipt.get("read_at"),
            "delivery_summary": receipt.get("summary"),
        })
    return result
