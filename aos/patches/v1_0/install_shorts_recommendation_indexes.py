"""Install bounded-query indexes for Shorts recommendation serving.

Schema-only by design: MariaDB DDL implicitly commits, so this patch performs
no data reconciliation or writes before adding indexes.
"""

from __future__ import annotations

from collections.abc import Sequence

import frappe


INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("AOS Short View", "idx_short_view_user_recent", ("user", "last_seen_at", "short")),
    ("AOS Short View", "idx_short_view_session_recent", ("session_id", "last_seen_at", "short")),
    ("AOS Short Event", "idx_short_event_user_recent", ("user", "creation", "event_type", "short")),
    ("AOS Short Event", "idx_short_event_session_recent", ("session_id", "creation", "event_type", "short")),
    ("AOS Short Like", "idx_short_like_user_short", ("user", "short")),
    ("AOS Short Like", "idx_short_like_short_user", ("short", "user")),
    ("AOS Short Save", "idx_short_save_user_recent", ("user", "creation", "short")),
    ("AOS Short Repost", "idx_short_repost_user_recent", ("user", "status", "creation", "short")),
    ("AOS Short Comment", "idx_short_comment_user_recent", ("user", "status", "creation", "short")),
    ("AOS Short Sound", "idx_short_sound_sound_short", ("sound", "short")),
)


def execute() -> None:
    for doctype, index_name, columns in INDEX_DEFINITIONS:
        _ensure_index(doctype, index_name, columns)


def _index_exists(doctype: str, index_name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (f"tab{doctype}", index_name),
        )
    )


def _ensure_columns(doctype: str, columns: Sequence[str]) -> None:
    if not frappe.db.table_exists(doctype):
        frappe.throw(f"Shorts recommendation schema table is missing: {doctype}")
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(
            f"Shorts recommendation schema columns are missing for {doctype}: {', '.join(missing)}"
        )


def _ensure_index(doctype: str, index_name: str, columns: tuple[str, ...]) -> None:
    _ensure_columns(doctype, columns)
    if _index_exists(doctype, index_name):
        return
    frappe.db.add_index(doctype, list(columns), index_name=index_name)
