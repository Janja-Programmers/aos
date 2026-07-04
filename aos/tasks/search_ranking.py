from __future__ import annotations

import frappe

from aos.services.search_ranking_service import (
    dispatch_search_index_job as _dispatch_search_index_job,
    reindex_active_ads as _reindex_active_ads,
    reindex_visible_shorts as _reindex_visible_shorts,
)


def dispatch_search_index_job(search_job_id: str | None = None, job_id: str | None = None):
    """Dispatch a persistent AOS Search Index Job to the search-ranking service.

    Automatic Frappe queue calls must use `search_job_id` because `job_id` is a
    reserved enqueue option in Frappe/RQ.
    """
    resolved_job_id = search_job_id or job_id
    if not resolved_job_id:
        frappe.throw("Missing search_job_id for search/ranking dispatch.")
    return _dispatch_search_index_job(resolved_job_id)


def reindex_active_ads(limit: int = 1000):
    return _reindex_active_ads(limit=limit, enqueue=True)


def reindex_visible_shorts(limit: int = 1000):
    return _reindex_visible_shorts(limit=limit, enqueue=True)


def refresh_search_indexes():
    """Scheduled safety refresh for hot searchable content."""
    try:
        ads = _reindex_active_ads(limit=1000, enqueue=True)
        shorts = _reindex_visible_shorts(limit=1000, enqueue=True)
        return {"ok": bool(ads.get("ok") and shorts.get("ok")), "ads": ads, "shorts": shorts}
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Search/ranking scheduled refresh failed")
        raise
