"""
Ads background jobs.

This module is intended for background/scheduled work.

Jobs:
- expire_ads: Mark Active ads as Expired when expires_on is in the past.
"""

from __future__ import annotations

import frappe

from aos.services.notification_service import NotificationService


def expire_ads() -> None:
    """
    Mark Active ads as Expired when their expires_on date has passed.

    Runs hourly via scheduler.
    Sends notifications to sellers.
    """

    try:
        # Fetch ads that will expire
        ads = frappe.get_all(
            "AOS Ad",
            filters={
                "status": "Active",
                "expires_on": ["<", frappe.utils.today()],
            },
            fields=["name", "seller", "title"],
        )

        if not ads:
            return

        ad_ids = [a.name for a in ads]

        # Bulk update
        frappe.db.sql(
            """
            UPDATE `tabAOS Ad`
            SET status = 'Expired'
            WHERE name IN %s
            """,
            (tuple(ad_ids),),
        )

        frappe.db.commit()

        # Notify sellers
        for ad in ads:
            if not ad.seller:
                continue

            NotificationService.notify_ad_expired(
                user=ad.seller,
                ad_id=ad.name,
                title=ad.title,
            )

        frappe.logger("aos").info(
            f"[AOS] expire_ads: marked {len(ad_ids)} ads as Expired."
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS expire_ads failed"
        )
