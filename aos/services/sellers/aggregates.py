"""Seller aggregate reconciliation facilities."""

from __future__ import annotations

from typing import Any

import frappe

from .observability import seller_log


def reconcile_seller_ad_counts(*, dry_run: bool = True, batch_size: int = 100) -> dict[str, Any]:
    """Reconcile stored ``total_ads`` from canonical public Active ads.

    Work is bounded and safe to rerun. The function does not commit; Bench
    callers control the outer transaction.
    """

    size = max(1, min(int(batch_size or 100), 500))
    offset = 0
    checked = 0
    drifted = 0
    updated = 0
    while True:
        sellers = frappe.get_all(
            "AOS Seller",
            fields=["name", "total_ads"],
            order_by="name asc",
            limit_start=offset,
            limit_page_length=size,
        )
        if not sellers:
            break
        names = [row.name for row in sellers]
        counts = {
            row.seller: int(row.count or 0)
            for row in frappe.db.sql(
                """
                SELECT seller, COUNT(*) AS count
                FROM `tabAOS Ad`
                WHERE seller IN %(sellers)s AND status = 'Active'
                GROUP BY seller
                """,
                {"sellers": tuple(names)},
                as_dict=True,
            )
        }
        for row in sellers:
            checked += 1
            expected = max(0, int(counts.get(row.name, 0)))
            current = max(0, int(row.total_ads or 0))
            if current == expected:
                continue
            drifted += 1
            if not dry_run:
                frappe.db.set_value(
                    "AOS Seller",
                    row.name,
                    "total_ads",
                    expected,
                    update_modified=False,
                )
                updated += 1
        offset += len(sellers)
        if len(sellers) < size:
            break
    seller_log(
        "seller.aggregate.reconciled",
        operation="seller_ad_count",
        outcome="success",
        count=drifted,
    )
    return {
        "dry_run": bool(dry_run),
        "checked": checked,
        "drifted": drifted,
        "updated": updated,
        "batch_size": size,
    }


def sync_seller_ad_count(seller_id: str) -> int:
    """Recompute one seller's Active-ad count inside the current transaction."""

    seller = str(seller_id or "").strip()
    if not seller:
        return 0
    count = int(frappe.db.count("AOS Ad", {"seller": seller, "status": "Active"}) or 0)
    frappe.db.set_value("AOS Seller", seller, "total_ads", max(0, count), update_modified=False)
    return max(0, count)
