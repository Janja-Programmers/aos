from __future__ import annotations

import frappe

from aos.services.analytics_pipeline_service import (
    dispatch_analytics_ingest_job as _dispatch_analytics_ingest_job,
    enqueue_analytics_ingest_dispatch,
)


def dispatch_analytics_ingest_job(analytics_job_id: str | None = None, job_id: str | None = None):
    """Dispatch one analytics ingest job.

    analytics_job_id is intentionally not named job_id in enqueue calls because
    job_id is reserved by Frappe/RQ as the Redis job id.
    """
    resolved_job_id = analytics_job_id or job_id
    if not resolved_job_id:
        frappe.throw("Missing analytics_job_id for analytics ingest dispatch.")
    return _dispatch_analytics_ingest_job(resolved_job_id)


def retry_queued_analytics_ingest_jobs(limit: int = 100) -> int:
    rows = frappe.get_all(
        "AOS Analytics Ingest Job",
        filters={
            "status": ["in", ["Queued", "Failed"]],
        },
        fields=["name", "attempt_count", "max_attempts"],
        order_by="creation asc",
        limit=limit,
    )

    queued = 0
    for row in rows:
        if int(row.get("attempt_count") or 0) >= int(row.get("max_attempts") or 3):
            continue
        try:
            enqueue_analytics_ingest_dispatch(row.name)
            queued += 1
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"AOS Analytics Ingest Retry Enqueue Failed: {row.name}",
            )

    return queued
