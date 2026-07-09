"""Add auth/account lookup indexes and uniqueness constraints."""

from __future__ import annotations

import frappe


AUTH_UNIQUE_CONSTRAINTS = [
    {
        "doctype": "AOS Email Verification",
        "fields": ["user", "purpose"],
        "name": "unique_aos_email_verification_user_purpose",
    },
]

AUTH_INDEXES = [
    {
        "doctype": "AOS Email Verification",
        "fields": ["email", "purpose", "is_used"],
        "name": "idx_aos_email_verification_email_purpose_used",
    },
    {
        "doctype": "AOS Email Verification",
        "fields": ["purpose", "is_used", "expires_at"],
        "name": "idx_aos_email_verification_purpose_used_expiry",
    },
    {
        "doctype": "AOS Email Verification",
        "fields": ["reset_token_hash", "reset_token_expires_at"],
        "name": "idx_aos_email_verification_reset_token_expiry",
    },
    {
        "doctype": "AOS Profile",
        "fields": ["account_status", "is_deleted", "restore_deadline"],
        "name": "idx_aos_profile_status_deleted_restore",
    },
]


def execute():
    _dedupe_email_verification()

    for constraint in AUTH_UNIQUE_CONSTRAINTS:
        _add_unique_if_missing(
            doctype=constraint["doctype"],
            fields=constraint["fields"],
            index_name=constraint["name"],
        )

    for index in AUTH_INDEXES:
        _add_index_if_missing(
            doctype=index["doctype"],
            fields=index["fields"],
            index_name=index["name"],
        )


def _dedupe_email_verification():
    doctype = "AOS Email Verification"
    if not _doctype_exists(doctype):
        return
    if not _has_columns(doctype, ["user", "purpose"]):
        return

    rows = frappe.db.sql(
        """
        SELECT user, purpose, GROUP_CONCAT(name ORDER BY modified DESC, creation DESC, name DESC) AS names
        FROM `tabAOS Email Verification`
        WHERE user IS NOT NULL AND user != ''
          AND purpose IS NOT NULL AND purpose != ''
        GROUP BY user, purpose
        HAVING COUNT(*) > 1
        """,
        as_dict=True,
    )

    for row in rows:
        names = [name for name in str(row.names or "").split(",") if name]
        stale = names[1:]
        for name in stale:
            frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)


def _add_unique_if_missing(*, doctype: str, fields: list[str], index_name: str):
    if not _doctype_exists(doctype):
        return
    _validate_columns(doctype, fields)
    if _index_exists(doctype, index_name, unique_only=True):
        return
    frappe.db.add_unique(doctype, fields, constraint_name=index_name)


def _add_index_if_missing(*, doctype: str, fields: list[str], index_name: str):
    if not _doctype_exists(doctype):
        return
    _validate_columns(doctype, fields)
    if _index_exists(doctype, index_name, unique_only=False):
        return
    frappe.db.add_index(doctype, fields, index_name=index_name)


def _validate_columns(doctype: str, fields: list[str]):
    missing = [field for field in fields if not frappe.db.has_column(doctype, field)]
    if missing:
        frappe.throw(f"Cannot add auth index on {doctype}. Missing columns: {', '.join(missing)}")


def _has_columns(doctype: str, fields: list[str]) -> bool:
    return all(frappe.db.has_column(doctype, field) for field in fields)


def _doctype_exists(doctype: str) -> bool:
    return bool(frappe.db.exists("DocType", doctype))


def _index_exists(doctype: str, index_name: str, *, unique_only: bool) -> bool:
    conditions = "AND NON_UNIQUE = 0" if unique_only else ""
    return bool(
        frappe.db.sql(
            f"""
            SELECT INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
              {conditions}
            LIMIT 1
            """,
            (f"tab{doctype}", index_name),
        )
    )
