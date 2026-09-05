"""Backfill Accounts public identity/profile fields and operational indexes."""

from __future__ import annotations

import frappe

from aos.services.accounts.identity import generate_public_account_id

INDEXES = {
    "idx_aos_profile_lifecycle": ("AOS Profile", ["account_status", "is_deleted", "restore_deadline"]),
    "idx_aos_profile_public_identity": ("AOS Profile", ["public_id"]),
}


def execute() -> None:
    if frappe.db.table_exists("AOS Profile"):
        _backfill_profiles()
    for name, (doctype, fields) in INDEXES.items():
        if not frappe.db.table_exists(doctype):
            continue
        existing_fields = [field for field in fields if frappe.db.has_column(doctype, field)]
        if len(existing_fields) != len(fields) or _index_exists(doctype, name):
            continue
        frappe.db.add_index(doctype, existing_fields, index_name=name)
    frappe.logger("aos.accounts", allow_site=True).info("accounts_schema_hardening_complete")


def _backfill_profiles() -> None:
    columns = {row[0] for row in frappe.db.sql("SHOW COLUMNS FROM `tabAOS Profile`")}
    if "public_id" not in columns:
        return
    rows = frappe.get_all(
        "AOS Profile",
        fields=["name", "user", "public_id", "display_name"],
        order_by="creation asc, name asc",
    )
    for row in rows:
        updates = {}
        if not row.public_id:
            for _ in range(8):
                candidate = generate_public_account_id()
                if not frappe.db.exists("AOS Profile", {"public_id": candidate}):
                    updates["public_id"] = candidate
                    break
        if "display_name" in columns and not row.display_name:
            display_name = frappe.db.get_value("User", row.user, "full_name") or "AOS User"
            updates["display_name"] = str(display_name).strip()[:80]
        if updates:
            frappe.db.set_value("AOS Profile", row.name, updates, update_modified=False)


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
