"""
Delete messages API (implementation).

Handles:
- delete_messages

Supported delete scopes:
- me:
    Hide selected messages only for the current user.

- everyone:
    Mark selected messages as deleted for both participants.
    Only the original sender can delete their own messages for everyone.

Notes:
- Messages are soft-deleted, not physically removed.
- Delete-for-me messages disappear from list_messages for that user.
- Delete-for-everyone messages remain visible as placeholders.
- Delete-for-everyone display text is viewer-specific:
    sender   -> You deleted this message
    receiver -> This message was deleted
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.services.chat.events import publish_after_commit
from aos.services.chat.repository import lock_conversations, lock_messages

from .constants import DELETE_MESSAGES_LIMIT_PER_MINUTE_PER_USER
from .preview import (
    recompute_conversation_preview_for_user,
    recompute_conversation_previews,
)
from .visibility import (
    get_deleted_for_everyone_display_text,
    get_deleted_for_user_at_field,
    get_deleted_for_user_field,
    get_other_participant,
)


VALID_DELETE_SCOPES = {
    "me",
    "everyone",
}


def _normalize_message_ids(value) -> List[str]:
    """
    Accept either:
    - message_ids: ["MSG-1", "MSG-2"]
    - message_id: "MSG-1"
    """

    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        return [str(v) for v in value if v]

    return []


def _get_conversation(conv_id: str):
    rows = frappe.db.sql(
        """SELECT name, participant_1, participant_2
        FROM `tabAOS Conversation` WHERE name = %s LIMIT 1 FOR UPDATE""",
        (conv_id,),
        as_dict=True,
    )
    return rows[0] if rows else None


def _fetch_messages(message_ids: List[str]) -> List[frappe._dict]:
    if not message_ids:
        return []

    return frappe.db.sql(
        """
        SELECT name, conversation, sender, message_type, content, ad, short, live,
               reply_to_message, has_attachments, is_forwarded, forwarded_from_message,
               forwarded_from_conversation, is_edited, edited_at, deleted_for_everyone,
               deleted_for_everyone_at, deleted_for_1, deleted_for_1_at, deleted_for_2,
               deleted_for_2_at, delivered_to_receiver_at, read_by_receiver_at, creation
        FROM `tabAOS Message`
        WHERE name IN %(message_ids)s
        ORDER BY name ASC
        """,
        {"message_ids": tuple(sorted(set(message_ids)))},
        as_dict=True,
    )


def _validate_messages_same_conversation(messages: List[frappe._dict]) -> str | None:
    conversation_ids = {m.conversation for m in messages if m.conversation}

    if len(conversation_ids) != 1:
        return None

    return next(iter(conversation_ids))


def _build_display_text_map(
    *,
    messages: List[frappe._dict],
    viewer: str,
) -> Dict[str, str]:
    """
    Build per-message deleted display text for one viewer.

    For the sender:
        You deleted this message

    For the receiver:
        This message was deleted
    """

    return {
        msg.name: get_deleted_for_everyone_display_text(
            sender=msg.sender,
            viewer=viewer,
        )
        for msg in messages
    }


def _build_deleted_payload(
    message_ids: List[str],
    *,
    delete_scope: str,
    display_text_map: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """
    Build delete response/realtime payload.

    display_text is kept for simple clients.
    display_text_by_message_id is safer for multi-delete payloads.
    """

    display_text_by_message_id = display_text_map or {}
    first_display_text = None

    if display_text_by_message_id:
        first_display_text = next(iter(display_text_by_message_id.values()))

    return {
        "delete_scope": delete_scope,
        "message_ids": message_ids,
        "display_text": first_display_text if delete_scope == "everyone" else None,
        "display_text_by_message_id": (
            display_text_by_message_id if delete_scope == "everyone" else {}
        ),
    }


def delete_messages_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "delete_messages", current_user),
        ttl_seconds=60,
        limit=DELETE_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
        message="Too many delete requests. Please slow down.",
    )
    if rl:
        return rl

    message_ids = _normalize_message_ids(
        kwargs.get("message_ids") or kwargs.get("message_id")
    )

    delete_scope = (kwargs.get("delete_scope") or "me").strip().lower()

    if not message_ids:
        return fail("message_ids is required.", error="VALIDATION_ERROR")

    if delete_scope not in VALID_DELETE_SCOPES:
        return fail(
            "delete_scope must be either 'me' or 'everyone'.",
            error="VALIDATION_ERROR",
        )

    try:
        # Deduplicate while preserving order.
        message_ids = list(dict.fromkeys(message_ids))

        messages = _fetch_messages(message_ids)

        if not messages:
            return fail("Messages not found.", error="NOT_FOUND")

        found_ids = {m.name for m in messages}
        missing_ids = [mid for mid in message_ids if mid not in found_ids]

        if missing_ids:
            return fail(
                "One or more messages were not found.",
                error="NOT_FOUND",
                data={"missing_message_ids": missing_ids},
            )

        conversation_id = _validate_messages_same_conversation(messages)

        if not conversation_id:
            return fail(
                "All messages must belong to the same conversation.",
                error="VALIDATION_ERROR",
            )

        # Deterministic lock order: conversation first, then messages by id.
        if conversation_id not in set(lock_conversations([conversation_id])):
            return fail("Conversation not found.", error="NOT_FOUND")
        locked_ids = set(lock_messages(message_ids))
        if locked_ids != set(message_ids):
            return fail("One or more messages were not found.", error="NOT_FOUND")

        # Re-read under lock so validation and mutation use authoritative state.
        messages = _fetch_messages(message_ids)
        conv = _get_conversation(conversation_id)

        if not conv:
            return fail("Conversation not found.", error="NOT_FOUND")

        if current_user not in (conv.participant_1, conv.participant_2):
            return fail("Not allowed.", error="PERMISSION_DENIED")

        now = now_datetime()

        deleted_ids: List[str] = []

        if delete_scope == "me":
            delete_field = get_deleted_for_user_field(conv, current_user)
            delete_at_field = get_deleted_for_user_at_field(conv, current_user)

            for msg in messages:
                # Already deleted-for-me: treat as idempotent success.
                if bool(getattr(msg, delete_field, 0)):
                    deleted_ids.append(msg.name)
                    continue

                frappe.db.set_value(
                    "AOS Message",
                    msg.name,
                    {
                        delete_field: 1,
                        delete_at_field: now,
                    },
                    update_modified=False,
                )

                deleted_ids.append(msg.name)

            recompute_conversation_preview_for_user(
                conversation_id=conversation_id,
                user=current_user,
            )

            return ok(
                "Messages deleted.",
                data=_build_deleted_payload(
                    deleted_ids,
                    delete_scope=delete_scope,
                ),
            )

        # delete_scope == "everyone"
        for msg in messages:
            if msg.message_type == "system":
                return fail(
                    "System messages cannot be deleted for everyone.",
                    error="VALIDATION_ERROR",
                    data={"message_id": msg.name},
                )

            if msg.sender != current_user:
                return fail(
                    "You can only delete your own messages for everyone.",
                    error="PERMISSION_DENIED",
                    data={"message_id": msg.name},
                )

        for msg in messages:
            # Already deleted-for-everyone: treat as idempotent success.
            if bool(msg.deleted_for_everyone):
                deleted_ids.append(msg.name)
                continue

            frappe.db.set_value(
                "AOS Message",
                msg.name,
                {
                    "deleted_for_everyone": 1,
                    "deleted_for_everyone_at": now,
                },
                update_modified=True,
            )

            # Keep in-memory row current for payload display maps.
            msg.deleted_for_everyone = 1
            msg.deleted_for_everyone_at = now

            deleted_ids.append(msg.name)

        recompute_conversation_previews(conversation_id)

        receiver = get_other_participant(conv, current_user)

        sender_display_text_map = _build_display_text_map(
            messages=messages,
            viewer=current_user,
        )

        receiver_display_text_map = _build_display_text_map(
            messages=messages,
            viewer=receiver,
        )

        realtime_payload = {
            "conversation_id": conversation_id,
            **_build_deleted_payload(
                deleted_ids,
                delete_scope="everyone",
                display_text_map=receiver_display_text_map,
            ),
        }

        publish_after_commit(
            event="aos_messages_deleted",
            message=realtime_payload,
            user=receiver,
        )

        return ok(
            "Messages deleted for everyone.",
            data=_build_deleted_payload(
                deleted_ids,
                delete_scope=delete_scope,
                display_text_map=sender_display_text_map,
            ),
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error("Chat operation failed.", "AOS Delete Messages Failed")
        return fail("Failed to delete messages.", error="INTERNAL_ERROR")
