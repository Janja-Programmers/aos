"""Current Moderation schema invariants reasserted after DocType sync."""
from __future__ import annotations

import frappe

DOCTYPE = "AOS Moderation Job"
INDEXES = (
    ("idx_aos_moderation_review_queue", ("status", "creation")),
    ("idx_aos_moderation_target", ("target_doctype", "target_name", "creation")),
)


def _has_index(name: str) -> bool:
    rows = frappe.db.sql(f"SHOW INDEX FROM `tab{DOCTYPE}` WHERE Key_name=%s", (name,), as_dict=True)
    return bool(rows)


def execute() -> None:
    if not frappe.db.exists("DocType", DOCTYPE):
        return
    for name, columns in INDEXES:
        if not _has_index(name):
            frappe.db.add_index(DOCTYPE, list(columns), index_name=name)
