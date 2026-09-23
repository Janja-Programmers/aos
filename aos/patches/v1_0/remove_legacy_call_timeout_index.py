"""Remove the deprecated Calls ringing-timeout index on upgraded sites.

This is intentionally a new forward patch: an earlier cleanup patch can already
be recorded as applied on sites where the post-migrate invariant subsequently
recreated the legacy index.
"""

from __future__ import annotations

import frappe


def execute() -> None:
    if not frappe.db.table_exists("AOS Call"):
        return

    exists = frappe.db.sql(
        """
        SELECT 1
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=DATABASE()
          AND TABLE_NAME='tabAOS Call'
          AND INDEX_NAME='idx_call_timeout'
        LIMIT 1
        """
    )
    if exists:
        frappe.db.sql("ALTER TABLE `tabAOS Call` DROP INDEX `idx_call_timeout`")
