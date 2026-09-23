"""Schema-only indexes for opaque Calls IDs and durable dispatch expiry."""

from __future__ import annotations

import frappe

from aos.patches.v1_0.install_call_indexes import _ensure_index


def execute() -> None:
    if not frappe.db.table_exists("AOS Call"):
        return

    _ensure_index("AOS Call", "uq_call_public_id", ("public_id",), unique=True)
    _ensure_index(
        "AOS Call",
        "idx_call_ring_expiry",
        ("status", "is_active", "ring_expires_at", "name"),
        unique=False,
    )
    _ensure_index(
        "AOS Call",
        "idx_call_provision_recovery",
        ("status", "is_active", "incoming_dispatched_at", "creation", "name"),
        unique=False,
    )
    _drop_legacy_timeout_index()


def _drop_legacy_timeout_index() -> None:
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
