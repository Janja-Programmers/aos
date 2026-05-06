"""
Conversation APIs (implementation).

Handles:
- get_or_create_conversation
- list_conversations
- delete_conversation
"""

from __future__ import annotations

from datetime import datetime
from typing import Tuple

import frappe
from frappe.utils import get_datetime

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
def _sort_participants(u1: str, u2: str) -> Tuple[str, str]:
    return tuple(sorted([u1, u2]))


def _last_message_sort_key(conv):
    """
    Normalize last_message_at into a datetime object.

    Frappe/MySQL may return:
    - datetime
    - string
    - None

    Python cannot compare datetime with string,
    so we normalize everything safely.
    """
    value = conv.get("last_message_at")

    if not value:
        return datetime.min

    if isinstance(value, datetime):
        return value

    try:
        return get_datetime(value)
    except Exception:
        return datetime.min


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
            # Reactivate if soft-deleted
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
                data={"id": existing.name},
            )

        # Create new conversation
        conv = frappe.new_doc("AOS Conversation")
        conv.participant_1 = p1
        conv.participant_2 = p2
        conv.insert(ignore_permissions=True)

        publish_presence_update_to_peers(current_user)

        return ok(
            "Conversation created.",
            data={"id": conv.name},
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

    limit = int(kwargs.get("limit") or 20)
    offset = int(kwargs.get("offset") or 0)

    try:
        # Fetch conversations
        convs_1 = frappe.get_all(
            "AOS Conversation",
            filters={
                "participant_1": current_user,
                "is_active_1": 1,
            },
            fields=[
                "name",
                "participant_1",
                "participant_2",
                "last_message",
                "last_message_at",
                "unread_count_1",
                "unread_count_2",
            ],
            limit_page_length=limit,
            limit_start=offset,
        )

        convs_2 = frappe.get_all(
            "AOS Conversation",
            filters={
                "participant_2": current_user,
                "is_active_2": 1,
            },
            fields=[
                "name",
                "participant_1",
                "participant_2",
                "last_message",
                "last_message_at",
                "unread_count_1",
                "unread_count_2",
            ],
            limit_page_length=limit,
            limit_start=offset,
        )

        conversations = convs_1 + convs_2

        if not conversations:
            return ok("Conversations fetched.", data=[])

        # Safe Python sorting
        conversations.sort(
            key=_last_message_sort_key,
            reverse=True,
        )

        # Collect other users
        other_users = set()

        for conv in conversations:
            other = (
                conv["participant_2"]
                if conv["participant_1"] == current_user
                else conv["participant_1"]
            )
            other_users.add(other)

        other_users = list(other_users)

        # Sellers
        sellers = frappe.get_all(
            "AOS Seller",
            filters={"user": ["in", other_users]},
            fields=["user", "shop_name", "avatar"],
        )
        seller_map = {s.user: s for s in sellers}

        # Users
        users = frappe.get_all(
            "User",
            filters={"name": ["in", other_users]},
            fields=["name", "full_name", "user_image"],
        )
        user_map = {u.name: u for u in users}

        results = []

        for conv in conversations:
            is_p1 = conv["participant_1"] == current_user

            other_user = (
                conv["participant_2"]
                if is_p1
                else conv["participant_1"]
            )

            seller = seller_map.get(other_user)

            if seller:
                display_name = seller.shop_name
                avatar = seller.avatar
            else:
                user = user_map.get(other_user)

                display_name = (
                    user.full_name
                    if user
                    else other_user
                )

                avatar = (
                    user.user_image
                    if user
                    else None
                )

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
                    "unread_count": unread,
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
