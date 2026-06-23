"""
Conversation APIs (implementation).

Handles:
- get_or_create_conversation
- list_conversations
- delete_conversation
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.api.shared.user_display import get_user_display, get_user_display_map

from .constants import (
    OPEN_CONVERSATION_LIMIT_PER_MINUTE_PER_USER,
    LIST_CONVERSATIONS_LIMIT_PER_MINUTE_PER_USER,
    DELETE_CONVERSATION_LIMIT_PER_MINUTE_PER_USER,
)

from .presence import publish_presence_update_to_peers


# Helpers
def _sort_participants(u1: str, u2: str) -> tuple[str, str]:
    return tuple(sorted([u1, u2]))


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


# get_or_create_conversation
def get_or_create_conversation_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:get_or_create:user:{current_user}",
        ttl_seconds=60,
        limit=OPEN_CONVERSATION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    other_user = kwargs.get("user")

    if not other_user:
        return fail("User is required.", code="VALIDATION_ERROR")

    if other_user == current_user:
        return fail(
            "Cannot start conversation with yourself.",
            code="VALIDATION_ERROR",
        )

    try:
        if not frappe.db.exists("User", other_user):
            return fail("User not found.", code="NOT_FOUND")

        p1, p2 = _sort_participants(current_user, other_user)

        existing = frappe.db.get_value(
            "AOS Conversation",
            {
                "participant_1": p1,
                "participant_2": p2,
            },
            ["name", "is_active_1", "is_active_2"],
            as_dict=True,
        )

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

            publish_presence_update_to_peers(current_user)

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
        conv.insert(ignore_permissions=True)

        publish_presence_update_to_peers(current_user)

        return ok(
            "Conversation created.",
            data=_build_conversation_response(
                conversation_id=conv.name,
                other_user=other_user,
            ),
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get/Create Conversation Failed",
        )
        frappe.db.rollback()
        return fail(
            "Failed to create conversation.",
            code="INTERNAL_ERROR",
        )


# list_conversations
def list_conversations_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:list:user:{current_user}",
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
        max_value=100000,
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
                modified DESC
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
            publish_presence_update_to_peers(current_user)
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

            results.append(
                {
                    "id": conv["name"],
                    "user": other_user,
                    "display_name": display_name,
                    "avatar": avatar,
                    "is_deleted": bool(user.get("is_deleted")),
                    "is_live": bool(user.get("is_live")) if not bool(user.get("is_deleted")) else False,
                    "live_id": user.get("live_id") if not bool(user.get("is_deleted")) else None,
                    "live_status": user.get("live_status") if not bool(user.get("is_deleted")) else None,
                    "last_message": last_message,
                    "last_message_at": last_message_at,
                    "last_sender": last_sender,
                    "last_sender_display_name": (
                        last_sender_user.get("display_name")
                        if last_sender_user
                        else last_sender
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
                        if last_sender_user and not bool(last_sender_user.get("is_deleted"))
                        else False
                    ),
                    "last_sender_live_id": (
                        last_sender_user.get("live_id")
                        if last_sender_user and not bool(last_sender_user.get("is_deleted"))
                        else None
                    ),
                    "unread_count": unread or 0,
                }
            )

        publish_presence_update_to_peers(current_user)

        return ok(
            "Conversations fetched.",
            data=results,
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Conversations Failed",
        )

        return fail(
            "Failed to fetch conversations.",
            code="INTERNAL_ERROR",
        )


# delete_conversation
def delete_conversation_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:delete:user:{current_user}",
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
            code="VALIDATION_ERROR",
        )

    try:
        conv = frappe.db.get_value(
            "AOS Conversation",
            conv_id,
            ["participant_1", "participant_2"],
            as_dict=True,
        )

        if not conv:
            return fail(
                "Conversation not found.",
                code="NOT_FOUND",
            )

        if current_user not in (
            conv.participant_1,
            conv.participant_2,
        ):
            return fail(
                "Not allowed.",
                code="PERMISSION_DENIED",
            )

        field = (
            "is_active_1"
            if conv.participant_1 == current_user
            else "is_active_2"
        )

        frappe.db.set_value(
            "AOS Conversation",
            conv_id,
            field,
            0,
            update_modified=False,
        )

        publish_presence_update_to_peers(current_user)

        return ok("Conversation deleted.")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Delete Conversation Failed",
        )

        frappe.db.rollback()

        return fail(
            "Failed to delete conversation.",
            code="INTERNAL_ERROR",
        )
