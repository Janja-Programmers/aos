"""Durable Ads discovery refresh dispatch."""

from __future__ import annotations

import frappe


def enqueue_discovery_refresh(ad_name: str, *, status: str, source: str) -> None:
    """Queue both existing discovery integrations after durable domain writes.

    Both downstream integrations already use the repository's transactional
    outbox/generation protections. Failures are logged and retried by their
    respective integration rather than changing the completed Ads mutation.
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
