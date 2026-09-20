from __future__ import annotations

import frappe
from frappe.utils import add_to_date, now_datetime

from aos.services.analytics_service import AnalyticsService
from aos.services.ranking_service import RankingService
from aos.services.shorts.hot_metrics import clear_short_hot_state, flush_hot_metrics
from aos.services.video_processing_service import recover_video_processing_jobs


def aggregate_short_metrics():
    flush_hot_metrics(limit=2000)
    AnalyticsService.aggregate_all_shorts(limit=1000)


def update_single_short_ranking(short_id: str):
    if frappe.db.exists("AOS Short", short_id):
        RankingService.update_short(short_id)


def update_short_ranking():
    RankingService.update_batch(limit=1000)


def recover_video_processing(*, limit: int = 100) -> dict[str, int]:
    return {"activated_retries": recover_video_processing_jobs(limit=max(1, min(int(limit), 500)))}


def maintain_short_integrity(*, limit: int = 500) -> dict[str, int]:
    """Bounded reconciliation for current Shorts state; no request-flow commits."""
    limit = max(1, min(int(limit), 2000))
    result = {"hot_flushed": flush_hot_metrics(limit=limit), "orphan_processing_failed": 0, "cancelled_deleted_jobs": 0, "reconciled": 0}
    cutoff = add_to_date(now_datetime(), hours=-2)

    stale = frappe.db.sql(
        """SELECT s.name FROM `tabAOS Short` s
             WHERE s.lifecycle_status='Processing'
               AND s.processing_status IN ('Queued','Processing','Retry Waiting')
               AND s.modified<%s
               AND NOT EXISTS (
                    SELECT 1 FROM `tabAOS Video Processing Job` j
                     WHERE j.short=s.name AND j.status IN ('Queued','Processing','Retry Waiting')
               )
             ORDER BY s.modified ASC LIMIT %s""",
        (cutoff, limit), as_dict=True,
    )
    for row in stale:
        frappe.db.set_value(
            "AOS Short", row.name,
            {"lifecycle_status": "Failed", "processing_status": "Failed", "processing_error": "PROCESSING_JOB_MISSING"},
            update_modified=True,
        )
        result["orphan_processing_failed"] += 1

    deleted_jobs = frappe.db.sql(
        """SELECT j.name,j.short FROM `tabAOS Video Processing Job` j
             INNER JOIN `tabAOS Short` s ON s.name=j.short
             WHERE s.lifecycle_status='Deleted' AND j.status IN ('Queued','Processing','Retry Waiting')
             ORDER BY j.modified ASC LIMIT %s""",
        (limit,), as_dict=True,
    )
    for row in deleted_jobs:
        frappe.db.set_value(
            "AOS Video Processing Job", row.name,
            {"status": "Cancelled", "active_key": None, "lease_owner": None, "lease_expires_at": None, "completed_at": now_datetime()},
            update_modified=False,
        )
        clear_short_hot_state(row.short)
        result["cancelled_deleted_jobs"] += 1

    rows = frappe.get_all(
        "AOS Short", filters={"lifecycle_status": ["!=", "Deleted"]}, fields=["name"],
        order_by="last_engagement_at desc, modified desc", limit=limit,
    )
    for row in rows:
        try:
            AnalyticsService.refresh_short_totals(row.name)
            result["reconciled"] += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Short integrity reconciliation failed: {row.name}")
    return result
