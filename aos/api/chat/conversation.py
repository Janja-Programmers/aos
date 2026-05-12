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


def _fetch_users(users: list[str]) -> dict[str, frappe._dict]:
    """
    Fetch lightweight user profile info for display.

    User.name remains the stable ID.
    User.full_name is used for UI display.
    """

    if not users:
        return {}

    rows = frappe.get_all(
        "User",
        filters={"name": ["in", users]},
        fields=["name", "full_name", "user_image"],
    )

    return {row.name: row for row in rows}


def _get_user_summary(user_id: str) -> dict:
    """
    Return a normalized user summary.

    This ensures the frontend can always render display_name
    and never needs to show the email/user id unless full_name is missing.
    """

    user = frappe.db.get_value(
        "User",
        user_id,
        ["name", "full_name", "user_image"],
        as_dict=True,
    )

    if not user:
        return {
            "user": user_id,
            "display_name": user_id,
            "avatar": None,
        }

    return {
        "user": user.name,
        "display_name": user.full_name or user.name,
        "avatar": user.user_image,
    }


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
    }


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
                last_message,
                last_message_at,
                unread_count_1,
                unread_count_2
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
                    WHEN last_message_at IS NULL THEN creation
                    ELSE last_message_at
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

        # Collect other users.
        other_users = set()

        for conv in conversations:
            other = (
                conv["participant_2"]
                if conv["participant_1"] == current_user
                else conv["participant_1"]
            )
            other_users.add(other)

        user_map = _fetch_users(list(other_users))

        results = []

        for conv in conversations:
            is_p1 = conv["participant_1"] == current_user

            other_user = (
                conv["participant_2"]
                if is_p1
                else conv["participant_1"]
            )

            user = user_map.get(other_user)

            display_name = (
                user.full_name
                if user and user.full_name
                else other_user
            )

            avatar = user.user_image if user else None

            unread = (
                conv["unread_count_1"]
                if is_p1
                else conv["unread_count_2"]
            )

            results.append(
                {
                    "id": conv["name"],
                    "user": other_user,
                    "display_name": display_name,
                    "avatar": avatar,
                    "last_message": conv["last_message"],
                    "last_message_at": conv["last_message_at"],
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
