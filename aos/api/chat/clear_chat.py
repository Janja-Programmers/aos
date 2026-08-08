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
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.services.accounts.identity import public_account_id_for_user

from .constants import CLEAR_CHAT_LIMIT_PER_MINUTE_PER_USER
from .preview import recompute_conversation_preview_for_user



CLEAR_CHAT_BATCH_SIZE = 500


def _clear_visible_messages_bounded(*, conversation_id: str, participant_index: int, changed_at) -> int:
    """Mark one viewer's history deleted in bounded, deterministic batches."""
    total = 0
    while True:
        if participant_index == 1:
            names = frappe.db.sql(
                """
                SELECT name FROM `tabAOS Message`
                WHERE conversation = %(conversation_id)s
                  AND IFNULL(deleted_for_1, 0) = 0
                ORDER BY creation ASC, name ASC
                LIMIT %(limit)s
                FOR UPDATE
                """,
                {"conversation_id": conversation_id, "limit": CLEAR_CHAT_BATCH_SIZE},
                pluck=True,
            )
            if not names:
                return total
            frappe.db.sql(
                """
                UPDATE `tabAOS Message`
                SET deleted_for_1 = 1, deleted_for_1_at = %(changed_at)s, modified = modified
                WHERE name IN %(names)s
                """,
                {"changed_at": changed_at, "names": tuple(names)},
            )
        else:
            names = frappe.db.sql(
                """
                SELECT name FROM `tabAOS Message`
                WHERE conversation = %(conversation_id)s
                  AND IFNULL(deleted_for_2, 0) = 0
                ORDER BY creation ASC, name ASC
                LIMIT %(limit)s
                FOR UPDATE
                """,
                {"conversation_id": conversation_id, "limit": CLEAR_CHAT_BATCH_SIZE},
                pluck=True,
            )
            if not names:
                return total
            frappe.db.sql(
                """
                UPDATE `tabAOS Message`
                SET deleted_for_2 = 1, deleted_for_2_at = %(changed_at)s, modified = modified
                WHERE name IN %(names)s
                """,
                {"changed_at": changed_at, "names": tuple(names)},
            )
        total += len(names)
        if len(names) < CLEAR_CHAT_BATCH_SIZE:
            return total

def _get_conversation(conv_id: str):
    rows = frappe.db.sql(
        """SELECT name, participant_1, participant_2
        FROM `tabAOS Conversation` WHERE name = %s LIMIT 1 FOR UPDATE""",
        (conv_id,),
        as_dict=True,
    )
    return rows[0] if rows else None


def clear_chat_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("chat", "clear_chat", current_user),
        ttl_seconds=60,
        limit=CLEAR_CHAT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many clear chat requests. Please slow down.",
    )
    if rl:
        return rl

    conv_id = kwargs.get("conversation_id")

    if not conv_id:
        return fail("conversation_id is required.", error="VALIDATION_ERROR")

    try:
        conv = _get_conversation(conv_id)

        if not conv:
            return fail("Conversation not found.", error="NOT_FOUND")

        if current_user not in (conv.participant_1, conv.participant_2):
            return fail("Not allowed.", error="PERMISSION_DENIED")

        now = now_datetime()

        # Use bounded static SQL shapes so very large histories do not run one
        # unbounded UPDATE. The conversation lock serializes this with sends.
        if current_user == conv.participant_1:
            cleared_count = _clear_visible_messages_bounded(
                conversation_id=conv_id, participant_index=1, changed_at=now
            )
            frappe.db.set_value(
                "AOS Conversation", conv_id, "unread_count_1", 0, update_modified=False
            )
        else:
            cleared_count = _clear_visible_messages_bounded(
                conversation_id=conv_id, participant_index=2, changed_at=now
            )
            frappe.db.set_value(
                "AOS Conversation", conv_id, "unread_count_2", 0, update_modified=False
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
                "cleared_for": public_account_id_for_user(current_user),
                "cleared_count": cleared_count,
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error("Chat operation failed.", "AOS Clear Chat Failed")
        return fail("Failed to clear chat.", error="INTERNAL_ERROR")
