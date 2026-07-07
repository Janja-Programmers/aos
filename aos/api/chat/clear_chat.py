"""
Clear chat API (implementation).

Handles:
- clear_chat

Behavior:
- Clears all visible messages in a conversation for the current user only.
- Does not affect the other participant.
- Does not physically delete message rows.
- Marks messages using deleted_for_1 / deleted_for_2 depending on viewer.
- Resets the current user's unread count to 0.
- Recomputes only the current user's conversation preview.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception

from .constants import CLEAR_CHAT_LIMIT_PER_MINUTE_PER_USER
from .preview import recompute_conversation_preview_for_user
from .visibility import (
    get_deleted_for_user_at_field,
    get_deleted_for_user_field,
)


def _get_conversation(conv_id: str):
    return frappe.db.get_value(
        "AOS Conversation",
        conv_id,
        ["name", "participant_1", "participant_2"],
        as_dict=True,
    )


def _get_unread_field_for_user(conv, user: str) -> str:
    if user == conv.participant_1:
        return "unread_count_1"

    if user == conv.participant_2:
        return "unread_count_2"

    frappe.throw("User is not a participant in this conversation")


def clear_chat_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:chat:clear:user:{current_user}",
        ttl_seconds=60,
        limit=CLEAR_CHAT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many clear chat requests. Please slow down.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")

    if not conv_id:
        return fail("conversation_id is required.", code="VALIDATION_ERROR")

    try:
        conv = _get_conversation(conv_id)

        if not conv:
            return fail("Conversation not found.", code="NOT_FOUND")

        if current_user not in (conv.participant_1, conv.participant_2):
            return fail("Not allowed.", code="PERMISSION_DENIED")

        delete_field = get_deleted_for_user_field(conv, current_user)
        delete_at_field = get_deleted_for_user_at_field(conv, current_user)
        unread_field = _get_unread_field_for_user(conv, current_user)

        now = now_datetime()

        # Hide every message still visible to the current user.
        # This includes deleted-for-everyone placeholder messages too,
        # because clear chat means remove the whole visible history for me.
        frappe.db.sql(
            f"""
            UPDATE `tabAOS Message`
            SET
                {delete_field} = 1,
                {delete_at_field} = %s,
                modified = modified
            WHERE
                conversation = %s
                AND IFNULL({delete_field}, 0) = 0
            """,
            (now, conv_id),
        )

        # Reset current user's unread count.
        frappe.db.set_value(
            "AOS Conversation",
            conv_id,
            {
                unread_field: 0,
            },
            update_modified=False,
        )

        # Current user should now have no visible latest message.
        # Other participant's preview is not affected.
        recompute_conversation_preview_for_user(
            conversation_id=conv_id,
            user=current_user,
        )

        return ok(
            "Chat cleared.",
            data={
                "conversation_id": conv_id,
                "cleared_for": current_user,
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return safe_fail_from_exception(ex, fallback="Invalid request.", code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Clear Chat Failed",
        )
        frappe.db.rollback()
        return fail("Failed to clear chat.", code="INTERNAL_ERROR")
