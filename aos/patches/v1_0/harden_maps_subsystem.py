"""Install-safe Maps schema/data hardening."""

from __future__ import annotations

import frappe

BATCH_SIZE = 500


def execute(batch_size: int = BATCH_SIZE) -> None:
    table = "tabAOS Seller"
    if not frappe.db.table_exists("AOS Seller"):
        return
    columns = set(frappe.db.get_table_columns("AOS Seller"))
    if "location_version" not in columns:
        return
    size = max(1, min(int(batch_size or BATCH_SIZE), 2000))
    cursor = ""
    while True:
        rows = frappe.db.sql(
            f"""
            SELECT name
            FROM `{table}`
            WHERE name > %s
              AND (`location_version` IS NULL OR `location_version` < 0)
            ORDER BY name ASC
            LIMIT %s
            """,
            (cursor, size),
            as_dict=True,
        )
        if not rows:
            break
        names = [row["name"] for row in rows]
        placeholders = ", ".join(["%s"] * len(names))
        frappe.db.sql(
            f"UPDATE `{table}` SET `location_version` = 0 WHERE name IN ({placeholders})",
            tuple(names),
        )
        cursor = names[-1]
