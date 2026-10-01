from __future__ import annotations

import frappe

DOCTYPE = "AOS Transactional Outbox"
INDEXES: dict[str, list[str]] = {
    "idx_aos_outbox_publish_due": ["status", "next_attempt_at", "creation"],
    "idx_aos_outbox_lease": ["status", "lease_expires_at"],
    "idx_aos_outbox_job": ["job_doctype", "job_name"],
    "idx_aos_outbox_service_status": ["service_type", "status"],
    "idx_aos_outbox_callback_deadline": ["status", "callback_deadline_at"],
}


def execute() -> None:
    if not frappe.db.table_exists(DOCTYPE):
        return
    for name, fields in INDEXES.items():
        present = frappe.db.sql(
            "SELECT 1 FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1",
            (f"tab{DOCTYPE}", name),
        )
        if not present:
            frappe.db.add_index(DOCTYPE, fields, index_name=name)
