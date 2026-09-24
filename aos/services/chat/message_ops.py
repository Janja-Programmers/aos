"""Canonical message send/history operations for direct and group Chat."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.services.chat.constants import LIST_MESSAGES_LIMIT_PER_MINUTE_PER_USER, SEND_MESSAGE_LIMIT_PER_MINUTE_PER_USER
from aos.api.shared.auth import require_login
from aos.api.shared.blocking import ensure_not_blocked
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.chat.cursors import decode_cursor, encode_cursor
from aos.services.chat.errors import ChatError
from aos.services.chat.events import publish_after_commit
from aos.services.chat.lock import authorize_locked_conversation
from aos.services.chat.membership import ACTIVE, DIRECT, active_members, require_active_membership
from aos.services.chat.projections import (
    fetch_ads,
    fetch_lives,
    fetch_shorts,
    live_shareable,
    message_preview,
    serialize_messages,
    short_shareable,
)
from aos.services.chat.repository import lock_conversations
from aos.services.chat.shared_objects import ad_is_shareable_to_users
from aos.services.media.media_service import MediaService
from aos.services.notifications.service import NotificationService
from aos.services.seller_response_metrics import enqueue_conversation_response_metrics_refresh

MAX_PAGE = 100


def _rate(operation: str, user: str, limit: int):
    return rate_limit(
        key=rate_limit_key("chat", operation, user),
        ttl_seconds=60,
        limit=limit,
        message="Too many Chat requests. Please try again shortly.",
    )


def _idempotency_digest(*, sender: str, conversation_id: str, key: str | None, operation: str = "send") -> str | None:
    raw = str(key or "").strip()
    if not raw:
        return None
    material = "\x1f".join(["chat", operation, conversation_id, sender, raw])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _request_hash(*, content: str, attachments: list[dict[str, str]], ad: str | None, short: str | None,
                  live: str | None, reply_to_message: str | None, forwarded_from_message: str | None = None) -> str:
    payload = {
        "content": content,
        "attachments": [item["media_id"] for item in attachments],
        "ad": ad,
        "short": short,
        "live": live,
        "reply_to_message": reply_to_message,
        "forwarded_from_message": forwarded_from_message,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _message_fields() -> list[str]:
    return [
        "name", "conversation", "sender", "message_type", "content", "ad", "short", "live", "call_id",
        "reply_to_message", "has_attachments", "recipient_count", "is_forwarded", "forwarded_from_message",
        "forwarded_from_conversation", "is_edited", "edited_at", "original_content", "deleted_for_everyone",
        "deleted_for_everyone_at", "idempotency_key", "idempotency_request_hash", "creation",
    ]


def _fetch_message(message_id: str):
    return frappe.db.get_value("AOS Message", message_id, _message_fields(), as_dict=True)


def _participant_users(conversation_id: str) -> list[str]:
    return [str(row.user) for row in active_members(conversation_id)]


def _ensure_direct_social_policy(conversation, *, sender: str, recipients: list[str]):
    if conversation.conversation_type != DIRECT:
        return None
    if not recipients:
        return fail("Conversation participant is unavailable.", error="CHAT_NOT_FOUND", http_status=404)
    return ensure_not_blocked(current_user=sender, target_user=recipients[0], action="message")


def _validate_reply(*, conversation_id: str, user: str, reply_to_message: str | None, membership) -> None:
    if not reply_to_message:
        return
    row = frappe.db.sql(
        """
        SELECT m.name, m.conversation, m.creation, mus.hidden_at
        FROM `tabAOS Message` m
        LEFT JOIN `tabAOS Message User State` mus ON mus.message=m.name AND mus.user=%(user)s
        WHERE m.name=%(message)s LIMIT 1
        """,
        {"user": user, "message": reply_to_message},
        as_dict=True,
    )
    if not row or row[0].conversation != conversation_id or row[0].hidden_at:
        raise ChatError("Reply message not found.", code="CHAT_NOT_FOUND", http_status=404)
    visible_from = membership.visible_from
    cleared_before = membership.cleared_before
    if visible_from and row[0].creation < visible_from:
        raise ChatError("Reply message not found.", code="CHAT_NOT_FOUND", http_status=404)
    if cleared_before and row[0].creation <= cleared_before:
        raise ChatError("Reply message not found.", code="CHAT_NOT_FOUND", http_status=404)


def _prepare_attachments(*, user: str, attachments: list[dict[str, str]]) -> list[str]:
    media = MediaService()
    ids: list[str] = []
    for item in attachments:
        media_id = str(item["media_id"])
        media.assert_media_ready_for_attach(media_id=media_id, user=user, purpose="chat_attachment")
        ids.append(media_id)
    return ids


def _validate_shared(*, sender: str, recipients: list[str], ad: str | None, short: str | None, live: str | None,
                     require_live_active: bool) -> None:
    audience = [sender, *recipients]
    if ad and not ad_is_shareable_to_users(ad, users=audience):
        raise ChatError("Ad is not available.", code="CHAT_NOT_FOUND", http_status=404)
    if short and not short_shareable(short, users=audience):
        raise ChatError("Short is not available.", code="CHAT_NOT_FOUND", http_status=404)
    if live and not live_shareable(live, users=audience, require_active=require_live_active):
        raise ChatError("Live is not available.", code="CHAT_NOT_FOUND", http_status=404)


def _determine_type(*, content: str, attachments: list[dict[str, str]], ad: str | None, short: str | None, live: str | None) -> str:
    parts = sum(bool(value) for value in (content, attachments, ad, short, live))
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


def _reaction_maps(message_ids: list[str], *, viewer: str):
    if not message_ids:
        return {}, {}
    rows = frappe.db.sql(
        """
        SELECT message, emoji, COUNT(*) AS reaction_count,
               MAX(CASE WHEN user=%(viewer)s THEN 1 ELSE 0 END) AS reacted_by_me
        FROM `tabAOS Message Reaction`
        WHERE message IN %(ids)s
        GROUP BY message, emoji
        ORDER BY message ASC, reaction_count DESC, emoji ASC
        """,
        {"ids": tuple(message_ids), "viewer": viewer},
        as_dict=True,
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    mine: dict[str, str] = {}
    for row in rows:
        grouped.setdefault(str(row.message), []).append(
            {"emoji": row.emoji, "count": int(row.reaction_count or 0), "reacted_by_me": bool(row.reacted_by_me)}
        )
        if row.reacted_by_me:
            mine[str(row.message)] = str(row.emoji)
    return grouped, mine


def _starred(message_ids: list[str], *, viewer: str) -> set[str]:
    if not message_ids:
        return set()
    rows = frappe.get_all(
        "AOS Message Star",
        filters={"message": ["in", message_ids], "user": viewer},
        fields=["message"],
        limit=max(1, len(message_ids)),
    )
    return {str(row.message) for row in rows}


def _receipt_map(rows: list[Any], *, viewer: str, conversation_type: str, participant_users: list[str]) -> dict[str, dict[str, Any]]:
    ids = [str(row.name) for row in rows]
    if not ids:
        return {}
    states = frappe.get_all(
        "AOS Message User State",
        filters={"message": ["in", ids]},
        fields=["message", "user", "delivered_at", "read_at"],
        limit=max(1, len(ids) * max(1, min(len(participant_users), 256))),
    )
    by_message: dict[str, list[Any]] = {}
    for state in states:
        by_message.setdefault(str(state.message), []).append(state)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        mid = str(row.name)
        own = next((s for s in by_message.get(mid, []) if s.user == viewer), None)
        payload: dict[str, Any] = {
            "delivered_at": own.delivered_at if own and row.sender != viewer else None,
            "read_at": own.read_at if own and row.sender != viewer else None,
        }
        if row.sender == viewer:
            recipient_states = [s for s in by_message.get(mid, []) if s.user != viewer]
            delivered = [s for s in recipient_states if s.delivered_at]
            read = [s for s in recipient_states if s.read_at]
            if conversation_type == DIRECT:
                peer = next((s for s in recipient_states if s.user != viewer), None)
                payload["delivered_at"] = peer.delivered_at if peer else None
                payload["read_at"] = peer.read_at if peer else None
            payload["summary"] = {
                "recipient_count": int(row.recipient_count or 0),
                "delivered_count": len(delivered),
                "read_count": len(read),
            }
        result[mid] = payload
    return result


def _serialize_one(message_id: str, *, viewer: str, conversation_type: str, participant_users: list[str]) -> dict[str, Any]:
    row = _fetch_message(message_id)
    if not row:
        raise ChatError("Message not found.", code="CHAT_NOT_FOUND", http_status=404)
    reactions, mine = _reaction_maps([message_id], viewer=viewer)
    return serialize_messages(
        [row],
        viewer=viewer,
        conversation_type=conversation_type,
        reactions_map=reactions,
        my_reactions=mine,
        starred=_starred([message_id], viewer=viewer),
        receipt_map=_receipt_map([row], viewer=viewer, conversation_type=conversation_type, participant_users=participant_users),
    )[0]


def _attach_media(*, message_id: str, user: str, media_ids: list[str]) -> None:
    media = MediaService()
    for idx, media_id in enumerate(media_ids):
        link = frappe.new_doc("AOS Message Attachment")
        link.message = message_id
        link.media = media_id
        link.sort_order = idx
        link.insert(ignore_permissions=True)
        media.attach_media(
            media_id=media_id,
            user=user,
            purpose="chat_attachment",
            attached_doctype="AOS Message",
            attached_name=message_id,
            attached_field="attachments",
        )


def _update_conversation_after_send(*, conversation_id: str, sender: str, message_id: str, preview: str, sent_at, recipients: list[str]):
    frappe.db.set_value(
        "AOS Conversation",
        conversation_id,
        {"last_message": preview, "last_message_at": sent_at, "last_sender": sender},
        update_modified=False,
    )
    frappe.db.sql(
        """
        UPDATE `tabAOS Conversation Participant`
        SET is_hidden=0,
            last_visible_message=%(message)s,
            last_visible_message_at=%(sent_at)s,
            last_visible_sender=%(sender)s,
            unread_count=CASE WHEN user=%(sender)s THEN unread_count ELSE COALESCE(unread_count,0)+1 END
        WHERE conversation=%(conversation)s AND status='active'
        """,
        {"message": message_id, "sent_at": sent_at, "sender": sender, "conversation": conversation_id},
    )


def _notify_recipients(*, conversation_id: str, message_id: str, sender: str, preview: str, recipients: list[str]):
    if not recipients:
        return
    lock_rows = frappe.get_all(
        "AOS Conversation Participant",
        filters={"conversation": conversation_id, "user": ["in", recipients], "status": ACTIVE},
        fields=["user", "is_locked"],
        limit=max(1, len(recipients)),
    )
    locked = {str(row.user) for row in lock_rows if int(row.is_locked or 0)}
    for recipient in recipients:
        publish_after_commit(
            event="aos_new_message",
            message={"conversation_id": conversation_id, "message_id": message_id},
            user=recipient,
        )
        try:
            NotificationService.notify_new_message(
                user=recipient,
                sender=sender,
                conversation_id=conversation_id,
                preview=preview,
                message_id=message_id,
                private_preview=recipient in locked,
            )
        except Exception:
            frappe.log_error("Chat notification creation failed.", "AOS Chat notification failure")


def send_message_for_user(*, current_user: str, require_live_active: bool = False, **kwargs):
    conv_id = str(kwargs.get("conversation_id") or "")
    authorize_locked_conversation(user=current_user, conversation_id=conv_id, lock_token=kwargs.get("lock_token"))
    lock_conversations([conv_id])
    conversation, membership = require_active_membership(conv_id, current_user, for_update=True)
    participants = active_members(conv_id)
    participant_users = [str(row.user) for row in participants]
    recipients = [user for user in participant_users if user != current_user]
    if policy_error := _ensure_direct_social_policy(conversation, sender=current_user, recipients=recipients):
        return policy_error

    content = str(kwargs.get("content") or "").strip()
    attachments = kwargs.get("attachments") or []
    ad = str(kwargs.get("ad") or "").strip() or None
    short = str(kwargs.get("short") or "").strip() or None
    live = str(kwargs.get("live") or "").strip() or None
    reply = str(kwargs.get("reply_to_message") or "").strip() or None
    if not any((content, attachments, ad, short, live)):
        return fail("Message content is required.", error="CHAT_INVALID_REQUEST", http_status=422)
    _validate_reply(conversation_id=conv_id, user=current_user, reply_to_message=reply, membership=membership)
    _validate_shared(sender=current_user, recipients=recipients, ad=ad, short=short, live=live, require_live_active=require_live_active)
    media_ids = _prepare_attachments(user=current_user, attachments=attachments)

    digest = _idempotency_digest(sender=current_user, conversation_id=conv_id, key=kwargs.get("idempotency_key"))
    request_hash = _request_hash(
        content=content, attachments=attachments, ad=ad, short=short, live=live, reply_to_message=reply
    )
    if digest:
        existing = frappe.db.get_value("AOS Message", {"idempotency_key": digest}, _message_fields(), as_dict=True)
        if existing:
            if str(existing.idempotency_request_hash or "") != request_hash:
                return fail("Idempotency key was reused for a different message.", error="CHAT_CONFLICT", http_status=409)
            return ok(
                "Message already sent.",
                data=_serialize_one(existing.name, viewer=current_user, conversation_type=conversation.conversation_type, participant_users=participant_users),
            )

    msg = frappe.new_doc("AOS Message")
    msg.conversation = conv_id
    msg.sender = current_user
    msg.message_type = _determine_type(content=content, attachments=attachments, ad=ad, short=short, live=live)
    msg.content = content or None
    msg.ad = ad
    msg.short = short
    msg.live = live
    msg.reply_to_message = reply
    msg.has_attachments = 1 if media_ids else 0
    msg.recipient_count = len(recipients)
    msg.idempotency_key = digest
    msg.idempotency_request_hash = request_hash if digest else None
    try:
        msg.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        if not digest:
            raise
        existing = frappe.db.get_value("AOS Message", {"idempotency_key": digest}, _message_fields(), as_dict=True)
        if not existing or str(existing.idempotency_request_hash or "") != request_hash:
            return fail("Idempotency key was reused for a different message.", error="CHAT_CONFLICT", http_status=409)
        return ok(
            "Message already sent.",
            data=_serialize_one(existing.name, viewer=current_user, conversation_type=conversation.conversation_type, participant_users=participant_users),
        )
    if media_ids:
        _attach_media(message_id=msg.name, user=current_user, media_ids=media_ids)

    ad_map = fetch_ads([ad] if ad else [], viewer=current_user)
    short_map = fetch_shorts([short] if short else [], viewer=current_user)
    live_map = fetch_lives([live] if live else [], viewer=current_user)
    preview = message_preview(
        content=content, has_attachments=bool(media_ids), ad=ad, short=short, live=live,
        ad_preview=ad_map.get(ad) if ad else None,
        short_preview=short_map.get(short) if short else None,
        live_preview=live_map.get(live) if live else None,
    )
    sent_at = msg.creation or now_datetime()
    _update_conversation_after_send(
        conversation_id=conv_id, sender=current_user, message_id=msg.name, preview=preview, sent_at=sent_at, recipients=recipients
    )
    _notify_recipients(
        conversation_id=conv_id, message_id=msg.name, sender=current_user, preview=preview, recipients=recipients
    )
    if conversation.conversation_type == DIRECT and len(participant_users) == 2:
        enqueue_conversation_response_metrics_refresh(
            participant_1=participant_users[0], participant_2=participant_users[1]
        )
    from aos.services.chat.presence_ops import schedule_presence_update_to_peers
    schedule_presence_update_to_peers(current_user)
    return ok(
        "Message sent.",
        data=_serialize_one(msg.name, viewer=current_user, conversation_type=conversation.conversation_type, participant_users=participant_users),
    )


def send_message_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if limited := _rate("send_message", current_user, SEND_MESSAGE_LIMIT_PER_MINUTE_PER_USER):
        return limited
    return send_message_for_user(current_user=current_user, **kwargs)


def list_messages_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if limited := _rate("list_messages", current_user, LIST_MESSAGES_LIMIT_PER_MINUTE_PER_USER):
        return limited
    conv_id = kwargs.get("conversation_id")
    authorize_locked_conversation(user=current_user, conversation_id=conv_id, lock_token=kwargs.get("lock_token"))
    conversation, membership = require_active_membership(conv_id, current_user)
    limit = int(kwargs.get("limit") or 50)
    cursor = decode_cursor(kwargs.get("cursor"), kind="message", required_keys=("created_at", "id"))
    params: dict[str, Any] = {
        "conversation": conv_id,
        "user": current_user,
        "visible_from": membership.visible_from,
        "cleared_before": membership.cleared_before,
        "limit": limit + 1,
    }
    cursor_sql = ""
    if cursor:
        params.update({"cursor_at": cursor["created_at"], "cursor_id": cursor["id"]})
        cursor_sql = "AND (m.creation < %(cursor_at)s OR (m.creation=%(cursor_at)s AND m.name < %(cursor_id)s))"
    rows = frappe.db.sql(
        f"""
        SELECT m.name, m.conversation, m.sender, m.message_type, m.content, m.ad, m.short, m.live, m.call_id,
               m.reply_to_message, m.has_attachments, m.recipient_count, m.is_forwarded,
               m.forwarded_from_message, m.forwarded_from_conversation, m.is_edited, m.edited_at,
               m.deleted_for_everyone, m.deleted_for_everyone_at, m.creation
        FROM `tabAOS Message` m
        LEFT JOIN `tabAOS Message User State` mus ON mus.message=m.name AND mus.user=%(user)s
        WHERE m.conversation=%(conversation)s
          AND m.creation >= %(visible_from)s
          AND (%(cleared_before)s IS NULL OR m.creation > %(cleared_before)s)
          AND mus.hidden_at IS NULL
          {cursor_sql}
        ORDER BY m.creation DESC, m.name DESC
        LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )
    more = len(rows) > limit
    page = rows[:limit]
    ids = [str(row.name) for row in page]
    reactions, mine = _reaction_maps(ids, viewer=current_user)
    participant_users = _participant_users(conv_id)
    items = serialize_messages(
        page,
        viewer=current_user,
        conversation_type=conversation.conversation_type,
        reactions_map=reactions,
        my_reactions=mine,
        starred=_starred(ids, viewer=current_user),
        receipt_map=_receipt_map(page, viewer=current_user, conversation_type=conversation.conversation_type, participant_users=participant_users),
    )
    next_cursor = (
        encode_cursor("message", {"created_at": str(page[-1].creation), "id": str(page[-1].name)})
        if more and page
        else None
    )
    return ok("Messages loaded.", data={"items": items, "next_cursor": next_cursor})
