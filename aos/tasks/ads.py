"""Ads background jobs.

This module is intended for background/scheduled work (similar to tasks.fx).

Jobs:
- expire_ads: Mark Active ads as Expired when expires_on is in the past.
"""

from __future__ import annotations

import frappe
from frappe.utils import today


def expire_ads() -> None:
    """Mark ads as Expired when their expires_on date has passed."""

    try:
        d = today()  # date string

        # Fast path: single SQL update.
        frappe.db.sql(
            """
            UPDATE `tabAOS Ad`
            SET status = 'Expired'
            WHERE status = 'Active'
              AND expires_on IS NOT NULL
              AND expires_on < %s
            """,
            (d,),
        )

        affected = 0
        try:
            affected = int(frappe.db.sql("SELECT ROW_COUNT()", as_list=True)[0][0])
        except Exception:
            affected = 0

        frappe.db.commit()

        if affected:
            frappe.logger().info(f"AOS expire_ads: marked {affected} ads as Expired.")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS expire_ads failed")
