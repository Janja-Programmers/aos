"""
Message star APIs (implementation).

Handles:
- set_message_star
- list_starred_messages

Rules:
- Stars are private to the current user.
- One user can star a message only once.
- User must be a participant in the message conversation.
- Messages deleted for the current user cannot be starred.
- Deleted-for-everyone messages cannot be newly starred.
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.services.chat.repository import lock_conversations, lock_messages
from aos.services.chat.cursors import decode_cursor, encode_cursor

from .constants import (
    SET_MESSAGE_STAR_LIMIT_PER_MINUTE_PER_USER,
    LIST_STARRED_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
)

from .message import (
    _clean_int,
    _fetch_ads_bulk,
    _fetch_reply_messages_bulk,
    _fetch_shorts_bulk,
    _fetch_lives_bulk,
    _fetch_users,
    _is_deleted_for_everyone,
    _serialize_attachments_bulk,
    _serialize_message,
)

from .reactions import (
    fetch_message_reaction_summaries,
    fetch_my_reactions,
)

from .visibility import get_deleted_for_user_field


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


def _get_existing_star(*, message_id: str, user: str) -> str | None:
    return frappe.db.get_value(
        "AOS Message Star",
        {
            "message": message_id,
            "user": user,
        },
        "name",
    )


def _validate_message_can_be_starred(msg, current_user: str):
    if current_user not in (msg.participant_1, msg.participant_2):
        return fail("Not allowed.", error="PERMISSION_DENIED")

    if _is_deleted_for_everyone(msg):
        return fail(
            "Deleted messages cannot be starred.",
            error="VALIDATION_ERROR",
        )

    delete_field = get_deleted_for_user_field(msg, current_user)

    if bool(getattr(msg, delete_field, 0)):
        return fail(
            "You cannot star a message deleted for you.",
            error="VALIDATION_ERROR",
        )

    return None


def _fetch_starred_message_ids(message_ids: List[str], user: str) -> set[str]:
    if not message_ids:
        return set()

    unique_ids = list({message_id for message_id in message_ids if message_id})

    if not unique_ids:
        return set()

    rows = frappe.get_all(
        "AOS Message Star",
        filters={
            "message": ["in", unique_ids],
            "user": user,
        },
        fields=["message"],
        limit=max(1, len(unique_ids)),
    )

    return {row.message for row in rows}


def _serialize_starred_messages(
    *,
    messages: List[frappe._dict],
    current_user: str,
) -> List[Dict[str, Any]]:
    if not messages:
        return []

    message_ids = [m.name for m in messages]

    visible_message_ids = [
        m.name for m in messages if not _is_deleted_for_everyone(m)
    ]

    reply_message_ids = list(
        {
            m.reply_to_message
            for m in messages
            if getattr(m, "reply_to_message", None)
            and not _is_deleted_for_everyone(m)
        }
    )

    reply_map = _fetch_reply_messages_bulk(reply_message_ids)

    user_ids = list({m.sender for m in messages if m.sender})

    for replied in reply_map.values():
        if replied.sender:
            user_ids.append(replied.sender)

    user_ids = list(set(user_ids))

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

    starred_ids = _fetch_starred_message_ids(message_ids, current_user)

    reaction_summaries = fetch_message_reaction_summaries(
        message_ids=visible_message_ids,
        viewer=current_user,
    )

    my_reactions = fetch_my_reactions(
        message_ids=visible_message_ids,
        user=current_user,
    )

    attachments_map = _serialize_attachments_bulk(visible_message_ids, current_user=current_user)
    user_map = _fetch_users(user_ids, viewer=current_user)
    ad_map = _fetch_ads_bulk(ad_ids, viewer=current_user)

    short_ids = [
        str(value) for value in [
            *[getattr(m, "short", None) for m in messages if not _is_deleted_for_everyone(m)],
            *[getattr(r, "short", None) for r in reply_map.values() if not _is_deleted_for_everyone(r)],
        ] if value
    ]
    live_ids = [
        str(value) for value in [
            *[getattr(m, "live", None) for m in messages if not _is_deleted_for_everyone(m)],
            *[getattr(r, "live", None) for r in reply_map.values() if not _is_deleted_for_everyone(r)],
        ] if value
    ]
    short_map = _fetch_shorts_bulk(short_ids, viewer=current_user)
    live_map = _fetch_lives_bulk(live_ids, viewer=current_user)

    results: List[Dict[str, Any]] = []

    for msg in messages:
        payload = _serialize_message(
            msg,
            attachments_map=attachments_map,
            user_map=user_map,
            ad_map=ad_map,
            short_map=short_map,
            live_map=live_map,
            current_user=current_user,
            reply_map=reply_map,
            is_starred=msg.name in starred_ids,
            reactions=reaction_summaries.get(msg.name, []),
            my_reaction=my_reactions.get(msg.name),
        )
        # Starred-message navigation needs the public conversation id so clients
        # can reopen the exact thread without exposing any private participant
        # identity. The caller is already a participant in this conversation.
        payload["conversation_id"] = msg.conversation
        results.append(payload)

    return results


def set_message_star_impl(**kwargs):
    """Set the caller's private star state to the requested value.

    This is a desired-state mutation: retrying the same request is a no-op and
    cannot accidentally invert the state after a network retry.
    """

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "set_message_star", current_user),
        ttl_seconds=60,
        limit=SET_MESSAGE_STAR_LIMIT_PER_MINUTE_PER_USER,
        message="Too many star requests. Please slow down.",
    )
    if rl:
        return rl

    message_id = kwargs.get("message_id")
    starred = bool(kwargs.get("starred"))
    if not message_id:
        return fail("message_id is required.", error="VALIDATION_ERROR")

    try:
        conversation_id = frappe.db.get_value("AOS Message", message_id, "conversation")
        if not conversation_id:
            return fail("Message not found.", error="NOT_FOUND")
        lock_conversations([conversation_id])
        if message_id not in set(lock_messages([message_id])):
            return fail("Message not found.", error="NOT_FOUND")

        msg = _get_message_with_conversation(message_id)
        if not msg:
            return fail("Message not found.", error="NOT_FOUND")

        validation_error = _validate_message_can_be_starred(msg, current_user)
        if validation_error:
            return validation_error

        existing_star = _get_existing_star(message_id=message_id, user=current_user)
        if starred:
            if not existing_star:
                star = frappe.new_doc("AOS Message Star")
                star.message = message_id
                star.conversation = msg.conversation
                star.user = current_user
                star.insert(ignore_permissions=True)
        elif existing_star:
            frappe.delete_doc("AOS Message Star", existing_star, ignore_permissions=True)

        return ok(
            "Message star state updated.",
            data={
                "message_id": message_id,
                "conversation_id": msg.conversation,
                "is_starred": starred,
            },
        )

    except frappe.DuplicateEntryError:
        if starred:
            return ok(
                "Message star state updated.",
                data={
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "is_starred": True,
                },
            )
        raise
    except frappe.ValidationError as ex:
        return safe_fail_from_exception(
            ex, fallback="Invalid request.", error="VALIDATION_ERROR"
        )
    except Exception:
        frappe.log_error("Chat operation failed.", "AOS Set Message Star Failed")
        return fail("Failed to update message star.", error="INTERNAL_ERROR")


def list_starred_messages_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "list_starred_messages", current_user),
        ttl_seconds=60,
        limit=LIST_STARRED_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    limit = _clean_int(kwargs.get("limit"), default=30, min_value=1, max_value=100)
    conversation_id = kwargs.get("conversation_id")
    cursor = decode_cursor(
        kwargs.get("cursor"),
        kind="starred_messages",
        required_keys=("starred_at", "message_id"),
    )

    try:
        params: Dict[str, Any] = {
            "current_user": current_user,
            "conversation_id": conversation_id or None,
            "cursor_starred_at": cursor.get("starred_at") if cursor else None,
            "cursor_message_id": cursor.get("message_id") if cursor else None,
            "fetch_limit": limit + 1,
        }
        messages = frappe.db.sql(
            """
            SELECT
                m.name, m.conversation, m.sender, m.content, m.message_type,
                m.ad, m.short, m.live, m.call_id, m.reply_to_message, m.has_attachments,
                m.is_forwarded, m.forwarded_from_message, m.forwarded_from_conversation,
                m.is_edited, m.edited_at,
                m.deleted_for_everyone, m.deleted_for_everyone_at,
                m.deleted_for_1, m.deleted_for_1_at,
                m.deleted_for_2, m.deleted_for_2_at,
                m.delivered_to_receiver_at, m.read_by_receiver_at, m.creation,
                s.creation AS starred_at
            FROM `tabAOS Message Star` s
            INNER JOIN `tabAOS Message` m ON m.name = s.message
            INNER JOIN `tabAOS Conversation` c ON c.name = s.conversation
            WHERE s.user = %(current_user)s
              AND (
                    (c.participant_1 = %(current_user)s AND IFNULL(m.deleted_for_1, 0) = 0)
                 OR (c.participant_2 = %(current_user)s AND IFNULL(m.deleted_for_2, 0) = 0)
              )
              AND (%(conversation_id)s IS NULL OR s.conversation = %(conversation_id)s)
              AND (
                    %(cursor_starred_at)s IS NULL
                 OR s.creation < %(cursor_starred_at)s
                 OR (s.creation = %(cursor_starred_at)s AND m.name < %(cursor_message_id)s)
              )
            ORDER BY s.creation DESC, m.name DESC
            LIMIT %(fetch_limit)s
            """,
            params,
            as_dict=True,
        )

        has_more = len(messages) > limit
        page = messages[:limit]
        results = _serialize_starred_messages(messages=page, current_user=current_user)
        for payload, msg in zip(results, page, strict=False):
            payload["starred_at"] = msg.starred_at

        next_cursor = None
        if has_more:
            last = page[-1]
            next_cursor = encode_cursor(
                kind="starred_messages",
                values={
                    "starred_at": str(last.starred_at),
                    "message_id": last.name,
                },
            )

        return ok(
            "Starred messages fetched.",
            data={"items": results, "next_cursor": next_cursor},
        )

    except Exception:
        frappe.log_error("Chat operation failed.", "AOS List Starred Messages Failed")
        return fail("Failed to fetch starred messages.", error="INTERNAL_ERROR")

