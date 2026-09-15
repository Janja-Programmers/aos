"""Durable Ads discovery refresh dispatch."""

from __future__ import annotations

import frappe


def enqueue_discovery_refresh(ad_name: str, *, status: str, source: str) -> None:
    """Queue both existing discovery integrations after durable domain writes.

    Search Ranking uses the transactional outbox; image indexing uses an
    after-commit idempotent queue job plus vector generations/reindex recovery.
    Failures never change the completed Ads mutation.
    """

    try:
        from aos.integrations.ai.image_search_tasks import enqueue_index_refresh_for_status

        enqueue_index_refresh_for_status(ad_name, status=status)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Ads image-search enqueue failed")

    try:
        from aos.services.search_ranking_service import enqueue_ad_search_index

        enqueue_ad_search_index(ad_name, source=source)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Ads search-ranking enqueue failed")


def enqueue_discovery_delete(
    ad_name: str, *, public_id: str, generation: str | None = None, source: str = "ad_delete"
) -> None:
    """Queue deletion from every derived discovery system after commit.

    The public ID and generation are captured before hard deletion. Derived
    indexes are never authoritative, so enqueue failures are logged and can be
    repaired by the maintenance/reindex commands without rolling back Ads.
    """
    try:
        from aos.integrations.ai.image_search_tasks import enqueue_delete_ad_vectors

        enqueue_delete_ad_vectors(public_id)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Ads image-search delete enqueue failed")

    try:
        from aos.services.search_ranking_service import enqueue_ad_search_delete

        enqueue_ad_search_delete(
            ad_name, public_id=public_id, generation=generation, source=source
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Ads search-ranking delete enqueue failed")
