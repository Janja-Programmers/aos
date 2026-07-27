"""Transactional wishlist counters and discovery-index refreshes."""

from __future__ import annotations

import frappe

from .constants import WISHLIST_STATUS_ACTIVE


def _enqueue_search_refresh(ad_id: str, *, source: str) -> None:
    """Best-effort transactional refresh of the ranking document.

    ``enqueue_ad_search_index`` persists through the repository's transactional
    outbox. A downstream outage must not invalidate a completed wishlist change,
    so enqueue failures are logged and retried through operational reconciliation.
    """

    try:
        from aos.services.search_ranking_service import enqueue_ad_search_index

        enqueue_ad_search_index(ad_id, source=source)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Wishlist Search Ranking Refresh Failed",
        )


def apply_wishlist_count_delta(ad_id: str, delta: int, *, source: str) -> None:
    """Atomically adjust an Ad's active wishlist count and refresh ranking state."""

    ad_id = str(ad_id or "").strip()
    delta = int(delta or 0)
    if not ad_id or delta == 0:
        return

    frappe.db.sql(
        """
        UPDATE `tabAOS Ad`
        SET wishlist_count = GREATEST(0, COALESCE(wishlist_count, 0) + %s)
        WHERE name = %s
        """,
        (delta, ad_id),
    )
    _enqueue_search_refresh(ad_id, source=source)


def recompute_wishlist_count(ad_id: str, *, source: str = "wishlist_reconcile") -> int:
    """Rebuild one Ad's active wishlist count from authoritative rows."""

    ad_id = str(ad_id or "").strip()
    if not ad_id or not frappe.db.exists("AOS Ad", ad_id):
        return 0

    count = int(
        frappe.db.count(
            "AOS Wishlist",
            {"ad": ad_id, "status": WISHLIST_STATUS_ACTIVE},
        )
        or 0
    )
    frappe.db.set_value(
        "AOS Ad",
        ad_id,
        "wishlist_count",
        count,
        update_modified=False,
    )
    _enqueue_search_refresh(ad_id, source=source)
    return count


def recompute_wishlist_counts(ad_ids: list[str], *, source: str = "wishlist_reconcile") -> int:
    """Rebuild counters for a bounded set of unique Ad identifiers."""

    unique_ids = sorted({str(ad_id or "").strip() for ad_id in ad_ids if str(ad_id or "").strip()})
    for ad_id in unique_ids:
        recompute_wishlist_count(ad_id, source=source)
    return len(unique_ids)
