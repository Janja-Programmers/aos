"""Idempotent current-schema indexes for the Verification domain.

This is a schema invariant installer for fresh/current AOS sites. It performs no
historical-data reconciliation and is safe to run from ``after_migrate``.
"""

from __future__ import annotations

import frappe

_INDEXES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("AOS Verification Request", "uq_aos_verification_user", ("user",), True),
    (
        "AOS Verification Request",
        "idx_aos_verification_review_queue",
        ("status", "submitted_on", "name"),
        False,
    ),
    (
        "AOS Verification Request",
        "idx_aos_verification_decisions",
        ("status", "verified_on", "name"),
        False,
    ),
    ("AOS Verification Document", "uq_aos_verification_document_media", ("media",), True),
)


def execute() -> None:
    for doctype, index_name, columns, unique in _INDEXES:
        if not _table_ready(doctype, columns) or _index_exists(doctype, index_name):
            continue
        if unique:
            frappe.db.add_unique(doctype, list(columns), constraint_name=index_name)
        else:
            frappe.db.add_index(doctype, list(columns), index_name=index_name)


def _table_ready(doctype: str, columns: tuple[str, ...]) -> bool:
    return bool(frappe.db.table_exists(doctype)) and all(
        frappe.db.has_column(doctype, column) for column in columns
    )


def _index_exists(doctype: str, index_name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (f"tab{doctype}", index_name),
        )
    )
