"""Install the canonical fresh-site Chat uniqueness and query indexes."""

from __future__ import annotations

from collections.abc import Sequence

import frappe

INDEXES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("AOS Conversation", "uniq_chat_pair_key", ("pair_key",), True),
    ("AOS Conversation", "idx_chat_conv_p1_active", ("participant_1", "is_active_1", "last_message_at_1", "name"), False),
    ("AOS Conversation", "idx_chat_conv_p2_active", ("participant_2", "is_active_2", "last_message_at_2", "name"), False),
    ("AOS Message", "uniq_chat_message_idempotency", ("idempotency_key",), True),
    ("AOS Message", "uniq_chat_call_message", ("call_id",), True),
    ("AOS Message", "idx_chat_message_history", ("conversation", "creation", "name"), False),
    ("AOS Message", "idx_chat_message_delivery", ("conversation", "sender", "delivered_to_receiver_at", "name"), False),
    ("AOS Message", "idx_chat_message_read", ("conversation", "sender", "read_by_receiver_at", "name"), False),
    ("AOS Message", "idx_chat_message_reply", ("reply_to_message", "creation", "name"), False),
    ("AOS Message", "idx_chat_message_ad", ("ad", "creation", "name"), False),
    ("AOS Message", "idx_chat_message_short", ("short", "creation", "name"), False),
    ("AOS Message", "idx_chat_message_live", ("live", "creation", "name"), False),
    ("AOS Message Attachment", "uniq_chat_attachment_message_media", ("message", "media"), True),
    ("AOS Message Attachment", "idx_chat_attachment_message", ("message", "sort_order", "name"), False),
    ("AOS Message Star", "uniq_chat_star_message_user", ("message", "user"), True),
    ("AOS Message Star", "idx_chat_star_user_created", ("user", "creation", "message"), False),
    ("AOS Message Reaction", "uniq_chat_reaction_message_user", ("message", "user"), True),
    ("AOS Message Reaction", "idx_chat_reaction_message", ("message", "emoji", "user"), False),
    ("AOS Message Translation", "uniq_chat_translation_cache", ("message", "target_language", "original_content_hash"), True),
    ("AOS Message Translation", "idx_chat_translation_conversation", ("conversation", "creation", "name"), False),
)


def execute() -> None:
    for doctype, name, columns, unique in INDEXES:
        _ensure_index(doctype, name, columns, unique=unique)


def _table(doctype: str) -> str:
    return f"tab{doctype}"


def _exists(doctype: str, name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1 FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
            LIMIT 1
            """,
            (_table(doctype), name),
        )
    )


def _ensure_columns(doctype: str, columns: Sequence[str]) -> None:
    if not frappe.db.table_exists(doctype):
        frappe.throw(f"Chat schema table is missing: {doctype}")
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(f"Chat schema columns are missing for {doctype}: {', '.join(missing)}")


def _assert_unique_ready(doctype: str, columns: Sequence[str], name: str) -> None:
    # Identifiers come exclusively from the static INDEXES tuple above; values
    # remain parameter-bound everywhere identifiers are not required by SQL.
    non_null = " AND ".join(f"`{column}` IS NOT NULL AND `{column}` != ''" for column in columns)
    groups = ", ".join(f"`{column}`" for column in columns)
    duplicate = frappe.db.sql(
        f"""
        SELECT 1 FROM `{_table(doctype)}`
        WHERE {non_null}
        GROUP BY {groups} HAVING COUNT(*) > 1 LIMIT 1
        """
    )
    if duplicate:
        frappe.throw(f"Cannot install Chat unique index {name}; duplicate data remains in {doctype}")


def _ensure_index(
    doctype: str,
    name: str,
    columns: tuple[str, ...],
    *,
    unique: bool,
) -> None:
    _ensure_columns(doctype, columns)
    if _exists(doctype, name):
        return
    if unique:
        _assert_unique_ready(doctype, columns, name)
    fields = list(columns)
    if unique:
        frappe.db.add_unique(doctype, fields, constraint_name=name)
    else:
        frappe.db.add_index(doctype, fields, index_name=name)
