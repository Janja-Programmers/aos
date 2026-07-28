"""Canonicalize legacy abbreviated Seller operating-hour day values.

The current public Seller contract uses full English day names. Historical
rows used ``Mon`` through ``Sun`` because the original child DocType Select
options were abbreviated. This post-model-sync patch is bounded, idempotent,
and deliberately does not commit.
"""

from __future__ import annotations

import frappe

_BATCH_SIZE = 250
_DAY_MAP = {
    "Mon": "Monday",
    "Tue": "Tuesday",
    "Wed": "Wednesday",
    "Thu": "Thursday",
    "Fri": "Friday",
    "Sat": "Saturday",
    "Sun": "Sunday",
}


def execute() -> None:
    frappe.reload_doc("aos", "doctype", "aos_seller_operating_hours", force=True)
    if not frappe.db.table_exists("AOS Seller Operating Hours"):
        return
    if not frappe.db.has_column("AOS Seller Operating Hours", "day_of_week"):
        return

    start_after = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, day_of_week
            FROM `tabAOS Seller Operating Hours`
            WHERE name > %(start_after)s
              AND day_of_week IN %(legacy_days)s
            ORDER BY name
            LIMIT %(limit)s
            """,
            {
                "start_after": start_after,
                "legacy_days": tuple(_DAY_MAP),
                "limit": _BATCH_SIZE,
            },
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            canonical = _DAY_MAP.get(str(row.day_of_week or "").strip())
            if canonical:
                frappe.db.set_value(
                    "AOS Seller Operating Hours",
                    row.name,
                    "day_of_week",
                    canonical,
                    update_modified=False,
                )
        start_after = rows[-1].name
