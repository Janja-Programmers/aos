"""
Ads background jobs.

This module is intended for background/scheduled work.

Jobs:
- expire_ads: Mark Active ads as Expired when expires_on is in the past.
"""

from __future__ import annotations

import frappe


def expire_ads() -> None:
    """
    Mark Active ads as Expired when their expires_on date has passed.

    Runs hourly via scheduler.
    Uses single SQL update for performance.
    """

    try:
        frappe.db.sql(
            """
            UPDATE `tabAOS Ad`
            SET status = 'Expired'
            WHERE status = 'Active'
              AND expires_on IS NOT NULL
              AND expires_on < CURDATE()
            """
        )

        affected = getattr(frappe.db._cursor, "rowcount", 0) or 0

        frappe.db.commit()

        if affected:
            frappe.logger("aos").info(
                f"[AOS] expire_ads: marked {affected} ads as Expired."
            )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS expire_ads failed"
        )
