"""Install indexes used by the canonical Live runtime and reconciliation jobs."""

from __future__ import annotations

from collections.abc import Sequence

import frappe

INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "AOS Live Stream",
        "idx_live_runtime_reconcile",
        ("status", "room_cleanup_pending", "last_reconciled_at", "modified", "name"),
    ),
    (
        "AOS Live Stream View",
        "idx_live_view_webhook_order",
        ("live_stream", "livekit_identity", "is_active", "last_livekit_event_at", "name"),
    ),
)


def execute() -> None:
    for doctype, name, columns in INDEXES:
        _ensure_index(doctype, name, columns)


def _ensure_index(doctype: str, name: str, columns: Sequence[str]) -> None:
    if not frappe.db.table_exists(doctype):
        frappe.throw(f"Live schema table is missing: {doctype}")
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(
            f"Live schema columns are missing for {doctype}: {', '.join(missing)}"
        )
    table = f"tab{doctype}"
    exists = frappe.db.sql(
        """
        SELECT 1 FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
        LIMIT 1
        """,
        (table, name),
    )
    if not exists:
        frappe.db.add_index(doctype, list(columns), index_name=name)
