"""Harden Verification schema indexes without destroying legacy evidence.

The existing application model keeps one Verification Request per account and
reuses that row for resubmission. This patch adds bounded lookup indexes and
installs uniqueness only when existing rows are already clean. Legacy duplicate
rows are deliberately preserved for operator review rather than deleted by an
automatic migration.
"""

from __future__ import annotations

import frappe

_REQUEST = "AOS Verification Request"
_DOCUMENT = "AOS Verification Document"

_INDEXES = {
    "idx_aos_verification_user_status": (_REQUEST, ["user", "status"]),
    "idx_aos_verification_review_queue": (_REQUEST, ["status", "modified", "name"]),
    "idx_aos_verification_reviewed": (_REQUEST, ["status", "verified_on", "name"]),
    "idx_aos_verification_document_media": (_DOCUMENT, ["media"]),
}


def execute() -> None:
    frappe.reload_doc("aos", "doctype", "aos_verification_request", force=True)
    frappe.reload_doc("aos", "doctype", "aos_verification_document", force=True)

    for name, (doctype, fields) in _INDEXES.items():
        if _table_ready(doctype, fields) and not _index_exists(doctype, name):
            frappe.db.add_index(doctype, fields, index_name=name)

    _add_unique_if_clean(
        doctype=_REQUEST,
        fields=["user"],
        name="uq_aos_verification_user",
        where_sql="user IS NOT NULL AND user != ''",
        group_sql="user",
    )
    _add_unique_if_clean(
        doctype=_DOCUMENT,
        fields=["media"],
        name="uq_aos_verification_document_media",
        where_sql="media IS NOT NULL AND media != ''",
        group_sql="media",
    )


def _table_ready(doctype: str, fields: list[str]) -> bool:
    return bool(frappe.db.table_exists(doctype)) and all(
        frappe.db.has_column(doctype, field) for field in fields
    )


def _add_unique_if_clean(
    *,
    doctype: str,
    fields: list[str],
    name: str,
    where_sql: str,
    group_sql: str,
) -> None:
    if not _table_ready(doctype, fields) or _index_exists(doctype, name, unique_only=True):
        return
    duplicates = frappe.db.sql(
        f"""
        SELECT {group_sql}, COUNT(*) AS row_count
        FROM `tab{doctype}`
        WHERE {where_sql}
        GROUP BY {group_sql}
        HAVING COUNT(*) > 1
        LIMIT 1
        """,
        as_dict=True,
    )
    if duplicates:
        frappe.log_error(
            "Verification uniqueness was not installed because legacy duplicate rows require operator review.",
            "Verification Hardening Migration",
        )
        return
    frappe.db.add_unique(doctype, fields, constraint_name=name)


def _index_exists(doctype: str, name: str, *, unique_only: bool = False) -> bool:
    unique_clause = "AND NON_UNIQUE = 0" if unique_only else ""
    return bool(
        frappe.db.sql(
            f"""
            SELECT INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
              {unique_clause}
            LIMIT 1
            """,
            (f"tab{doctype}", name),
        )
    )
