"""
Conversation APIs (implementation).

Handles:
- get_or_create_conversation
- list_conversations
- delete_conversation
"""

from __future__ import annotations

import hashlib

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.blocking import ensure_not_blocked, get_blocked_user_set
from aos.api.shared.account_status import ensure_account_active
from aos.services.accounts.identity import resolve_account_reference
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.user_display import get_user_display, get_user_display_map

from .constants import (
    OPEN_CONVERSATION_LIMIT_PER_MINUTE_PER_USER,
    LIST_CONVERSATIONS_LIMIT_PER_MINUTE_PER_USER,
    DELETE_CONVERSATION_LIMIT_PER_MINUTE_PER_USER,
)

from .presence import schedule_presence_update_to_peers


# Helpers
def _sort_participants(u1: str, u2: str) -> tuple[str, str]:
    return tuple(sorted([u1, u2]))


def _pair_key(u1: str, u2: str) -> str:
    p1, p2 = _sort_participants(u1, u2)
    return hashlib.sha256("\x1f".join([p1, p2]).encode("utf-8")).hexdigest()


def _get_conversation_by_pair(*, p1: str, p2: str, lock: bool = False):
    params = {"pair_key": _pair_key(p1, p2), "p1": p1, "p2": p2}
    query = """
        SELECT name, participant_1, participant_2, is_active_1, is_active_2
        FROM `tabAOS Conversation`
        WHERE pair_key = %(pair_key)s
           OR (participant_1 = %(p1)s AND participant_2 = %(p2)s)
        ORDER BY creation ASC, name ASC
        LIMIT 1
    """
    if lock:
        query += " FOR UPDATE"
    rows = frappe.db.sql(query, params, as_dict=True)
    return rows[0] if rows else None


def _clean_int(value, default: int, *, min_value: int, max_value: int) -> int:
    """
    Safely parse pagination values.
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


def _fetch_users(users: list[str]) -> dict[str, dict]:
    """Fetch display-safe user summaries for chat payloads."""

    return get_user_display_map(users)


def _get_user_summary(user_id: str) -> dict:
    """Return a display-safe user summary."""

    return get_user_display(user_id)

def _build_conversation_response(
    *,
    conversation_id: str,
    other_user: str,
) -> dict:
    """
    Build the common response payload returned when opening/creating a chat.
    """

    other = _get_user_summary(other_user)

    return {
        "id": conversation_id,
        "user": other["user"],
        "display_name": other["display_name"],
        "avatar": other["avatar"],
        "is_deleted": bool(other.get("is_deleted")),
        "is_live": bool(other.get("is_live")) if not bool(other.get("is_deleted")) else False,
        "live_id": other.get("live_id") if not bool(other.get("is_deleted")) else None,
        "live_status": other.get("live_status") if not bool(other.get("is_deleted")) else None,
    }


def _viewer_preview_fields(conv, current_user: str) -> tuple[str | None, object | None, str | None]:
    """
    Return viewer-specific conversation preview fields.

    Since AOS Conversation now stores separate last-visible-message previews
    for each participant, the API must choose the correct set based on the
    current viewer.
    """

    if conv["participant_1"] == current_user:
        return (
            conv.get("last_message_1"),
            conv.get("last_message_at_1"),
            conv.get("last_sender_1"),
        )

    return (
        conv.get("last_message_2"),
        conv.get("last_message_at_2"),
        conv.get("last_sender_2"),
    )


def _fetch_latest_outgoing_statuses(conversations, current_user: str) -> dict[str, dict]:
    """Return delivery/read state for viewer-authored conversation previews.

    Conversation previews are participant-specific because delete-for-me and
    clear-chat can make each side's latest visible message differ.  Resolve the
    latest visible message for only those previews whose sender is the current
    user in one bounded query (list_conversations is capped at 50 rows).
    """

    conversation_ids = []
    for conv in conversations:
        _preview, _preview_at, last_sender = _viewer_preview_fields(conv, current_user)
        if last_sender == current_user:
            conversation_ids.append(conv["name"])

    if not conversation_ids:
        return {}

    rows = frappe.db.sql(
        """
        SELECT
            c.name AS conversation_id,
            m.name AS message_id,
            m.delivered_to_receiver_at AS delivered_at,
            m.read_by_receiver_at AS read_at
        FROM `tabAOS Conversation` c
        LEFT JOIN `tabAOS Message` m
          ON m.name = (
                SELECT m2.name
                FROM `tabAOS Message` m2
                WHERE m2.conversation = c.name
                  AND m2.sender = %(current_user)s
                  AND (
                        (c.participant_1 = %(current_user)s AND IFNULL(m2.deleted_for_1, 0) = 0)
                     OR (c.participant_2 = %(current_user)s AND IFNULL(m2.deleted_for_2, 0) = 0)
                  )
                ORDER BY m2.creation DESC, m2.name DESC
                LIMIT 1
          )
        WHERE c.name IN %(conversation_ids)s
        """,
        {
            "current_user": current_user,
            "conversation_ids": tuple(conversation_ids),
        },
        as_dict=True,
    )

    return {
        row["conversation_id"]: {
            "message_id": row.get("message_id"),
            "delivered_at": row.get("delivered_at"),
            "read_at": row.get("read_at"),
        }
        for row in rows
    }


# get_or_create_conversation
def get_or_create_conversation_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "open_conversation", current_user),
        ttl_seconds=60,
        limit=OPEN_CONVERSATION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    other_reference = kwargs.get("user")

    if not other_reference:
        return fail("User is required.", error="VALIDATION_ERROR")

    other_user = resolve_account_reference(other_reference, allow_legacy=True)
    if not other_user:
        return fail("User not found.", error="NOT_FOUND", http_status=404)

    if other_user == current_user:
        return fail(
            "Cannot start conversation with yourself.",
            error="VALIDATION_ERROR",
        )

    try:
        if not frappe.db.exists("User", {"name": other_user, "enabled": 1}):
            return fail("User not found.", error="NOT_FOUND", http_status=404)

        target_state_error = ensure_account_active(other_user)
        if target_state_error:
            return fail("User not found.", error="NOT_FOUND", http_status=404)

        block_err = ensure_not_blocked(
            current_user=current_user,
            target_user=other_user,
            action="message",
        )
        if block_err:
            return block_err

        p1, p2 = _sort_participants(current_user, other_user)

        existing = _get_conversation_by_pair(p1=p1, p2=p2, lock=True)

        if existing:
            # Reactivate if soft-deleted for the current user.
            updates = {}

            if existing.is_active_1 == 0 and current_user == p1:
                updates["is_active_1"] = 1

            if existing.is_active_2 == 0 and current_user == p2:
                updates["is_active_2"] = 1

            if updates:
                frappe.db.set_value(
                    "AOS Conversation",
                    existing.name,
                    updates,
                    update_modified=False,
                )

            schedule_presence_update_to_peers(current_user)

            return ok(
                "Conversation fetched.",
                data=_build_conversation_response(
                    conversation_id=existing.name,
                    other_user=other_user,
                ),
            )

        # Create new conversation.
        conv = frappe.new_doc("AOS Conversation")
        conv.participant_1 = p1
        conv.participant_2 = p2
        conv.pair_key = _pair_key(p1, p2)
        try:
            conv.insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            existing = _get_conversation_by_pair(p1=p1, p2=p2, lock=True)
            if not existing:
                raise
            updates = {}
            if existing.is_active_1 == 0 and current_user == p1:
                updates["is_active_1"] = 1
            if existing.is_active_2 == 0 and current_user == p2:
                updates["is_active_2"] = 1
            if updates:
                frappe.db.set_value("AOS Conversation", existing.name, updates, update_modified=False)
            schedule_presence_update_to_peers(current_user)
            return ok(
                "Conversation fetched.",
                data=_build_conversation_response(
                    conversation_id=existing.name,
                    other_user=other_user,
                ),
            )

        schedule_presence_update_to_peers(current_user)

        return ok(
            "Conversation created.",
            data=_build_conversation_response(
                conversation_id=conv.name,
                other_user=other_user,
            ),
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error("Chat operation failed.", "AOS Get/Create Conversation Failed")
        return fail(
            "Failed to create conversation.",
            error="INTERNAL_ERROR",
        )


# list_conversations
def list_conversations_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "list_conversations", current_user),
        ttl_seconds=60,
        limit=LIST_CONVERSATIONS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    limit = _clean_int(
        kwargs.get("limit"),
        default=20,
        min_value=1,
        max_value=50,
    )

    offset = _clean_int(
        kwargs.get("offset"),
        default=0,
        min_value=0,
        max_value=10000,
    )

    try:
        conversations = frappe.db.sql(
            """
            SELECT
                name,
                participant_1,
                participant_2,
                last_message_1,
                last_message_at_1,
                last_sender_1,
                last_message_2,
                last_message_at_2,
                last_sender_2,
                unread_count_1,
                unread_count_2,
                creation,
                modified
            FROM `tabAOS Conversation`
            WHERE
                (
                    participant_1 = %(current_user)s
                    AND IFNULL(is_active_1, 1) = 1
                )
                OR
                (
                    participant_2 = %(current_user)s
                    AND IFNULL(is_active_2, 1) = 1
                )
            ORDER BY
                CASE
                    WHEN participant_1 = %(current_user)s
                        THEN COALESCE(last_message_at_1, creation)
                    ELSE COALESCE(last_message_at_2, creation)
                END DESC,
                modified DESC,
                name DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {
                "current_user": current_user,
                "limit": limit,
                "offset": offset,
            },
            as_dict=True,
        )

        if not conversations:
            schedule_presence_update_to_peers(current_user)
            return ok("Conversations fetched.", data=[])

        # Collect users needed for display:
        # - other participant
        # - viewer-specific last sender
        user_ids = set()

        for conv in conversations:
            is_p1 = conv["participant_1"] == current_user

            other = conv["participant_2"] if is_p1 else conv["participant_1"]
            user_ids.add(other)

            _, _, last_sender = _viewer_preview_fields(conv, current_user)
            if last_sender:
                user_ids.add(last_sender)

        user_map = _fetch_users(list(user_ids))
        blocked_users = get_blocked_user_set(current_user, user_ids)
        outgoing_statuses = _fetch_latest_outgoing_statuses(conversations, current_user)

        results = []

        for conv in conversations:
            is_p1 = conv["participant_1"] == current_user

            other_user = conv["participant_2"] if is_p1 else conv["participant_1"]

            user = user_map.get(other_user) or get_user_display(other_user)

            display_name = user.get("display_name")
            avatar = user.get("avatar")

            unread = conv["unread_count_1"] if is_p1 else conv["unread_count_2"]

            last_message, last_message_at, last_sender = _viewer_preview_fields(
                conv,
                current_user,
            )

            last_sender_user = user_map.get(last_sender) if last_sender else None
            outgoing_status = outgoing_statuses.get(conv["name"]) or {}
            other_blocked = other_user in blocked_users
            last_sender_blocked = bool(last_sender and last_sender in blocked_users)

            results.append(
                {
                    "id": conv["name"],
                    "user": user.get("user"),
                    "display_name": display_name,
                    "avatar": avatar,
                    "is_deleted": bool(user.get("is_deleted")),
                    "is_live": (
                        bool(user.get("is_live"))
                        if not other_blocked and not bool(user.get("is_deleted"))
                        else False
                    ),
                    "live_id": (
                        user.get("live_id")
                        if not other_blocked and not bool(user.get("is_deleted"))
                        else None
                    ),
                    "live_status": (
                        user.get("live_status")
                        if not other_blocked and not bool(user.get("is_deleted"))
                        else None
                    ),
                    "last_message": last_message,
                    "last_message_at": last_message_at,
                    "last_sender": (last_sender_user.get("user") if last_sender_user else None),
                    "last_sender_display_name": (
                        last_sender_user.get("display_name")
                        if last_sender_user
                        else None
                    ),
                    "last_sender_avatar": (
                        last_sender_user.get("avatar")
                        if last_sender_user
                        else None
                    ),
                    "last_sender_is_deleted": (
                        bool(last_sender_user.get("is_deleted"))
                        if last_sender_user
                        else False
                    ),
                    "last_sender_is_live": (
                        bool(last_sender_user.get("is_live"))
                        if last_sender_user
                        and not last_sender_blocked
                        and not bool(last_sender_user.get("is_deleted"))
                        else False
                    ),
                    "last_sender_live_id": (
                        last_sender_user.get("live_id")
                        if last_sender_user
                        and not last_sender_blocked
                        and not bool(last_sender_user.get("is_deleted"))
                        else None
                    ),
                    # Sender-only receipt state for WhatsApp-style conversation
                    # preview ticks. last_message_id lets realtime clients apply
                    # a receipt event only when it actually contains the current
                    # preview message, avoiding stale-event races with newer sends.
                    "last_message_id": outgoing_status.get("message_id"),
                    "last_message_is_mine": bool(last_sender == current_user),
                    "last_message_delivered_at": outgoing_status.get("delivered_at"),
                    "last_message_read_at": outgoing_status.get("read_at"),
                    "unread_count": unread or 0,
                }
            )

        schedule_presence_update_to_peers(current_user)

        return ok(
            "Conversations fetched.",
            data=results,
        )

    except Exception:
        frappe.log_error("Chat operation failed.", "AOS List Conversations Failed")

        return fail(
            "Failed to fetch conversations.",
            error="INTERNAL_ERROR",
        )


# delete_conversation
def delete_conversation_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "delete_conversation", current_user),
        ttl_seconds=60,
        limit=DELETE_CONVERSATION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")

    if not conv_id:
        return fail(
            "conversation_id is required.",
            error="VALIDATION_ERROR",
        )

    try:
        rows = frappe.db.sql(
            """
            SELECT name, participant_1, participant_2
            FROM `tabAOS Conversation`
            WHERE name = %s
            LIMIT 1 FOR UPDATE
            """,
            (conv_id,),
            as_dict=True,
        )
        conv = rows[0] if rows else None

        if not conv:
            return fail(
                "Conversation not found.",
                error="NOT_FOUND",
            )

        if current_user not in (
            conv.participant_1,
            conv.participant_2,
        ):
            return fail(
                "Not allowed.",
                error="PERMISSION_DENIED",
            )

        from frappe.utils import now_datetime
        from .clear_chat import _clear_visible_messages_bounded
        from .preview import recompute_conversation_preview_for_user

        participant_index = 1 if conv.participant_1 == current_user else 2
        active_field = "is_active_1" if participant_index == 1 else "is_active_2"
        unread_field = "unread_count_1" if participant_index == 1 else "unread_count_2"
        changed_at = now_datetime()

        # Deleting a conversation means the same user's existing history is
        # cleared as well as the list row being hidden. A later incoming
        # message reactivates the conversation, but old cleared messages stay
        # hidden for this user.
        cleared_count = _clear_visible_messages_bounded(
            conversation_id=conv_id,
            participant_index=participant_index,
            changed_at=changed_at,
        )
        frappe.db.set_value(
            "AOS Conversation",
            conv_id,
            {active_field: 0, unread_field: 0},
            update_modified=False,
        )
        recompute_conversation_preview_for_user(conversation_id=conv_id, user=current_user)

        schedule_presence_update_to_peers(current_user)

        return ok(
            "Conversation deleted.",
            data={"conversation_id": conv_id, "cleared_count": cleared_count},
        )

    except Exception:
        frappe.log_error("Chat operation failed.", "AOS Delete Conversation Failed")


        return fail(
            "Failed to delete conversation.",
            error="INTERNAL_ERROR",
        )
