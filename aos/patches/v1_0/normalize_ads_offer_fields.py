"""Normalize persisted Ads offer metadata after pricing hardening.

Frappe Currency columns can hydrate an unset value as zero. Older rows may also
retain offer metadata after changing away from Fixed pricing. The Currency
column is non-nullable, so its canonical empty sentinel is zero; both states
are canonicalized so lifecycle-only saves, especially moderation callbacks,
do not interpret a storage sentinel or stale hidden values as an active offer.
"""

from __future__ import annotations

import frappe


def execute() -> None:
    if not frappe.db.table_exists("AOS Ad"):
        return
    required = (
        "price_type",
        "offer_price",
        "offer_start_date",
        "offer_end_date",
        "offer_percent",
    )
    if not all(frappe.db.has_column("AOS Ad", field) for field in required):
        return

    frappe.db.sql(
        """
        UPDATE `tabAOS Ad`
        SET offer_price = 0,
            offer_start_date = NULL,
            offer_end_date = NULL,
            offer_percent = 0
        WHERE COALESCE(price_type, '') != 'Fixed'
           OR COALESCE(offer_price, 0) <= 0
        """
    )
    frappe.logger("aos.ads", allow_site=True).info("ads_offer_fields_normalized")
