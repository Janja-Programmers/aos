"""Database access helpers for Verification."""

from __future__ import annotations

import frappe

from .constants import VERIFICATION_DOCTYPE


def lock_request_for_user(user: str):
    rows = frappe.db.sql(
        f"""
        SELECT name
        FROM `tab{VERIFICATION_DOCTYPE}`
        WHERE user = %s
        ORDER BY creation DESC, name DESC
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


def get_latest_request_for_user(user: str):
    name = frappe.db.get_value(
        VERIFICATION_DOCTYPE,
        {"user": str(user or "").strip()},
        "name",
        order_by="creation desc",
    )
    return frappe.get_doc(VERIFICATION_DOCTYPE, name) if name else None
