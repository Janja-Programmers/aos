"""Idempotent schema-only index installation for reconciled Live data."""

from __future__ import annotations

from collections.abc import Sequence

import frappe

INDEXES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("AOS Live Stream", "uq_live_room_name", ("room_name",), True),
    ("AOS Live Stream", "idx_live_feed", ("status", "is_active", "started_at", "name"), False),
    ("AOS Live Stream", "idx_live_host_state", ("host_user", "status", "is_active", "name"), False),
    ("AOS Live Stream", "idx_live_cleanup", ("room_cleanup_pending", "modified", "name"), False),
    ("AOS Live Stream View", "uq_live_active_session", ("live_stream", "active_identity_key"), True),
    ("AOS Live Stream View", "idx_live_view_presence", ("live_stream", "is_active", "last_seen_at", "name"), False),
    ("AOS Live Stream View", "idx_live_view_identity", ("live_stream", "livekit_identity", "is_active"), False),
    ("AOS Live CoHost", "idx_live_cohost_state", ("live_stream", "status", "expires_at", "name"), False),
    ("AOS Live CoHost", "idx_live_cohost_user", ("user", "status", "modified", "name"), False),
    ("AOS Live Message", "idx_live_message_page", ("live_stream", "status", "creation", "name"), False),
    ("AOS Live Message", "idx_live_reply_page", ("parent_message", "status", "creation", "name"), False),
    ("AOS LiveKit Webhook Event", "idx_live_webhook_cleanup", ("status", "creation", "name"), False),
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
        frappe.throw(f"Live schema table is missing: {doctype}")
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(f"Live schema columns are missing for {doctype}: {', '.join(missing)}")


def _assert_unique_ready(doctype: str, columns: Sequence[str], name: str) -> None:
    non_null = " AND ".join(f"`{column}` IS NOT NULL" for column in columns)
    groups = ", ".join(f"`{column}`" for column in columns)
    duplicate = frappe.db.sql(
        f"""
        SELECT 1 FROM `{_table(doctype)}`
        WHERE {non_null}
        GROUP BY {groups} HAVING COUNT(*) > 1 LIMIT 1
        """
    )
    if duplicate:
        frappe.throw(f"Cannot install Live unique index {name}; duplicate data remains in {doctype}")


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
