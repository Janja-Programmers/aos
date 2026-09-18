"""Database access helpers for Verification."""

from __future__ import annotations

import frappe

from .constants import VERIFICATION_DOCTYPE

SUMMARY_FIELDS = [
    "user",
    "name",
    "verification_type",
    "status",
    "business_category",
    "verified_on",
    "modified",
]


def lock_request_for_user(user: str):
    rows = frappe.db.sql(
        f"""
        SELECT name
        FROM `tab{VERIFICATION_DOCTYPE}`
        WHERE user = %s
        LIMIT 1
        FOR UPDATE
        """,
        (str(user or "").strip(),),
        as_dict=True,
    )
    if not rows:
        return None
    return frappe.get_doc(VERIFICATION_DOCTYPE, rows[0].name)


def lock_request_by_name(name: str):
    """Lock and load one Verification request for a state transition."""
    clean = str(name or "").strip()
    if not clean:
        return None
    rows = frappe.db.sql(
        f"""
        SELECT name
        FROM `tab{VERIFICATION_DOCTYPE}`
        WHERE name = %s
        LIMIT 1
        FOR UPDATE
        """,
        (clean,),
        as_dict=True,
    )
    if not rows:
        return None
    return frappe.get_doc(VERIFICATION_DOCTYPE, rows[0].name)


def get_request_for_user(user: str):
    name = frappe.db.get_value(
        VERIFICATION_DOCTYPE,
        {"user": str(user or "").strip()},
        "name",
    )
    return frappe.get_doc(VERIFICATION_DOCTYPE, name) if name else None


def get_request_summary_for_user(user: str):
    """Project the current Verification state without hydrating the document."""
    clean = str(user or "").strip()
    if not clean:
        return None
    return frappe.db.get_value(
        VERIFICATION_DOCTYPE,
        {"user": clean},
        SUMMARY_FIELDS,
        as_dict=True,
    )


def get_request_summaries_for_users(users: list[str] | tuple[str, ...] | set[str]) -> dict[str, dict]:
    """Bulk-project canonical Verification state keyed by Frappe User name."""
    unique = sorted({str(user or "").strip() for user in users if str(user or "").strip()})
    if not unique:
        return {}
    rows = frappe.get_all(
        VERIFICATION_DOCTYPE,
        filters={"user": ["in", unique]},
        fields=SUMMARY_FIELDS,
        limit=len(unique),
    )
    return {str(row.user): row for row in rows if row.user}
