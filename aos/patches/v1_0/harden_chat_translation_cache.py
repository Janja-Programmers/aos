"""Make Chat translation cache source-aware and retry-safe on upgraded sites."""
from __future__ import annotations

import frappe

OLD_INDEX = "uniq_chat_translation_cache"
NEW_INDEX = "uniq_chat_translation_request_cache"
TABLE = "tabAOS Message Translation"


def _index_exists(name: str) -> bool:
    return bool(
        frappe.db.sql(
            """SELECT 1 FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1""",
            (TABLE, name),
        )
    )


def execute():
    if not frappe.db.table_exists("AOS Message Translation"):
        return
    required = ("request_source_language", "request_target_language")
    if any(not frappe.db.has_column("AOS Message Translation", field) for field in required):
        frappe.throw("Chat translation request-key columns are missing; run schema sync before this patch.")

    frappe.db.sql(
        f"""UPDATE `{TABLE}`
            SET request_source_language=COALESCE(NULLIF(request_source_language,''), COALESCE(NULLIF(source_language,''),'__default__')),
                request_target_language=COALESCE(NULLIF(request_target_language,''), target_language)
            WHERE request_source_language IS NULL OR request_source_language=''
               OR request_target_language IS NULL OR request_target_language=''"""
    )

    if _index_exists(OLD_INDEX):
        frappe.db.sql_ddl(f"ALTER TABLE `{TABLE}` DROP INDEX `{OLD_INDEX}`")

    if not _index_exists(NEW_INDEX):
        duplicates = frappe.db.sql(
            f"""SELECT 1 FROM `{TABLE}`
                WHERE message IS NOT NULL AND message!=''
                  AND request_source_language IS NOT NULL AND request_source_language!=''
                  AND request_target_language IS NOT NULL AND request_target_language!=''
                  AND original_content_hash IS NOT NULL AND original_content_hash!=''
                GROUP BY message, request_source_language, request_target_language, original_content_hash
                HAVING COUNT(*)>1 LIMIT 1"""
        )
        if duplicates:
            frappe.throw("Cannot install source-aware Chat translation cache uniqueness; duplicate rows remain.")
        frappe.db.add_unique(
            "AOS Message Translation",
            ["message", "request_source_language", "request_target_language", "original_content_hash"],
            constraint_name=NEW_INDEX,
        )
