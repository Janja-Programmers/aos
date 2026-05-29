"""
Message star APIs (implementation).

Handles:
- toggle_message_star
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
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import (
    TOGGLE_MESSAGE_STAR_LIMIT_PER_MINUTE_PER_USER,
    LIST_STARRED_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
)

from .message import (
    _clean_int,
    _fetch_ads_bulk,
    _fetch_reply_messages_bulk,
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
            m.reply_to_message,
            m.has_attachments,
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
        return fail("Not allowed.", code="PERMISSION_DENIED")

    if _is_deleted_for_everyone(msg):
        return fail(
            "Deleted messages cannot be starred.",
            code="VALIDATION_ERROR",
        )

    delete_field = get_deleted_for_user_field(msg, current_user)

    if bool(getattr(msg, delete_field, 0)):
        return fail(
            "You cannot star a message deleted for you.",
            code="VALIDATION_ERROR",
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

    attachments_map = _serialize_attachments_bulk(visible_message_ids)
    user_map = _fetch_users(user_ids)
    ad_map = _fetch_ads_bulk(ad_ids)

    results: List[Dict[str, Any]] = []

    for msg in messages:
        results.append(
            _serialize_message(
                msg,
                attachments_map=attachments_map,
                user_map=user_map,
                ad_map=ad_map,
                reply_map=reply_map,
                is_starred=msg.name in starred_ids,
                reactions=reaction_summaries.get(msg.name, []),
                my_reaction=my_reactions.get(msg.name),
            )
        )

    return results


def toggle_message_star_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:star:toggle:user:{current_user}",
        ttl_seconds=60,
        limit=TOGGLE_MESSAGE_STAR_LIMIT_PER_MINUTE_PER_USER,
        message="Too many star requests. Please slow down.",
    )
    if rl:
        return rl

    message_id = kwargs.get("message_id")

    if not message_id:
        return fail("message_id is required.", code="VALIDATION_ERROR")

    try:
        msg = _get_message_with_conversation(message_id)

        if not msg:
            return fail("Message not found.", code="NOT_FOUND")

        validation_error = _validate_message_can_be_starred(msg, current_user)
        if validation_error:
            return validation_error

        existing_star = _get_existing_star(
            message_id=message_id,
            user=current_user,
        )

        if existing_star:
            frappe.delete_doc(
                "AOS Message Star",
                existing_star,
                ignore_permissions=True,
            )

            return ok(
                "Message unstarred.",
                data={
                    "message_id": message_id,
                    "conversation_id": msg.conversation,
                    "is_starred": False,
                },
            )

        star = frappe.new_doc("AOS Message Star")
        star.message = message_id
        star.conversation = msg.conversation
        star.user = current_user
        star.insert(ignore_permissions=True)

        return ok(
            "Message starred.",
            data={
                "message_id": message_id,
                "conversation_id": msg.conversation,
                "is_starred": True,
            },
        )

    except frappe.DuplicateEntryError:
        frappe.db.rollback()

        return ok(
            "Message already starred.",
            data={
                "message_id": message_id,
                "is_starred": True,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Toggle Message Star Failed",
        )
        frappe.db.rollback()
        return fail("Failed to update message star.", code="INTERNAL_ERROR")


def _get_before_star_creation(*, before: str | None, current_user: str):
    """
    Resolve pagination cursor.

    Supports:
    - before = AOS Message Star name
    - before = AOS Message name
    """

    if not before:
        return None

    star_creation = frappe.db.get_value(
        "AOS Message Star",
        {
            "name": before,
            "user": current_user,
        },
        "creation",
    )

    if star_creation:
        return star_creation

    return frappe.db.get_value(
        "AOS Message Star",
        {
            "message": before,
            "user": current_user,
        },
        "creation",
    )


def list_starred_messages_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:star:list:user:{current_user}",
        ttl_seconds=60,
        limit=LIST_STARRED_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    limit = _clean_int(
        kwargs.get("limit"),
        default=30,
        min_value=1,
        max_value=100,
    )

    conversation_id = kwargs.get("conversation_id")
    before = kwargs.get("before")

    try:
        filters_sql = [
            "s.user = %(current_user)s",
            """
            (
                (c.participant_1 = %(current_user)s AND IFNULL(m.deleted_for_1, 0) = 0)
                OR
                (c.participant_2 = %(current_user)s AND IFNULL(m.deleted_for_2, 0) = 0)
            )
            """,
        ]

        params: Dict[str, Any] = {
            "current_user": current_user,
            "limit": limit,
        }

        if conversation_id:
            filters_sql.append("s.conversation = %(conversation_id)s")
            params["conversation_id"] = conversation_id

        before_creation = _get_before_star_creation(
            before=before,
            current_user=current_user,
        )

        if before:
            if not before_creation:
                return fail("Invalid 'before' cursor.", code="VALIDATION_ERROR")

            filters_sql.append("s.creation < %(before_creation)s")
            params["before_creation"] = before_creation

        where_clause = " AND ".join(filters_sql)

        messages = frappe.db.sql(
            f"""
            SELECT
                m.name,
                m.conversation,
                m.sender,
                m.content,
                m.message_type,
                m.ad,
                m.reply_to_message,
                m.has_attachments,
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

                s.name AS star_id,
                s.creation AS starred_at
            FROM `tabAOS Message Star` s
            INNER JOIN `tabAOS Message` m
                ON m.name = s.message
            INNER JOIN `tabAOS Conversation` c
                ON c.name = s.conversation
            WHERE {where_clause}
            ORDER BY s.creation DESC
            LIMIT %(limit)s
            """,
            params,
            as_dict=True,
        )

        if not messages:
            return ok("Starred messages fetched.", data=[])

        results = _serialize_starred_messages(
            messages=messages,
            current_user=current_user,
        )

        for payload, msg in zip(results, messages, strict=False):
            payload["star_id"] = msg.star_id
            payload["starred_at"] = msg.starred_at

        return ok("Starred messages fetched.", data=results)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Starred Messages Failed",
        )
        return fail("Failed to fetch starred messages.", code="INTERNAL_ERROR")
