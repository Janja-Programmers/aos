from __future__ import annotations

import frappe

from aos.services.analytics_service import AnalyticsService
from aos.services.ranking_service import RankingService


# ANALYTICS TASK
def aggregate_short_metrics():
    try:
        frappe.logger().info("[Shorts Task] Aggregating metrics")

        AnalyticsService.aggregate_all_shorts(limit=1000)

        frappe.logger().info("[Shorts Task] Metrics aggregation done")

    except Exception:
        frappe.log_error(
            "Shorts operation failed.",
            "Shorts Metrics Task Failed",
        )


# RANKING TASK
def update_short_ranking():
    try:
        frappe.logger().info("[Shorts Task] Updating ranking")

        RankingService.update_batch(limit=1000)

        frappe.logger().info("[Shorts Task] Ranking update done")

    except Exception:
        frappe.log_error(
            "Shorts operation failed.",
            "Shorts Ranking Task Failed",
        )



def recover_pending_audio_mixes(
    *,
    limit: int | None = None,
    stale_minutes: int | None = None,
) -> dict[str, int]:
    """Recreate missing durable remix jobs for ready Shorts.

    A Short must never remain indefinitely at ``pending`` or ``processing``
    without an active audio-reprocess job. The task is bounded, commit-free and
    safe to run repeatedly.
    """
    from frappe.utils import add_to_date, now_datetime

    from aos.services.video_processing_service import create_video_processing_job
    from aos.utils.aos_config import get_env_int

    batch_limit = int(
        limit
        or get_env_int(
            "AOS_SHORTS_AUDIO_MIX_RECOVERY_BATCH_LIMIT",
            50,
            min_value=1,
            max_value=500,
        )
    )
    age_minutes = int(
        stale_minutes
        if stale_minutes is not None
        else get_env_int(
            "AOS_SHORTS_AUDIO_MIX_STALE_MINUTES",
            10,
            min_value=1,
            max_value=1440,
        )
    )
    cutoff = add_to_date(now_datetime(), minutes=-max(0, age_minutes))
    rows = frappe.db.sql(
        """
        SELECT s.name
        FROM `tabAOS Short` s
        WHERE s.status = 'ready'
          AND s.audio_mix_status IN ('pending', 'processing')
          AND s.modified <= %s
          AND NOT EXISTS (
              SELECT 1
              FROM `tabAOS Video Processing Job` j
              WHERE j.short = s.name
                AND j.status IN ('Queued', 'Dispatching', 'Processing')
          )
        ORDER BY s.modified ASC, s.name ASC
        LIMIT %s
        """,
        (cutoff, max(1, min(batch_limit, 500))),
        as_dict=True,
    )

    result = {"checked": len(rows), "requeued": 0, "failed": 0}
    for index, row in enumerate(rows):
        savepoint = f"aos_audio_mix_recovery_{index}"
        frappe.db.savepoint(savepoint)
        try:
            create_video_processing_job(
                short_id=row.name,
                force=True,
                reason="audio_reprocess",
                enqueue=True,
            )
            result["requeued"] += 1
        except Exception:
            frappe.db.rollback(save_point=savepoint)
            frappe.db.set_value(
                "AOS Short",
                row.name,
                {
                    "audio_mix_status": "failed",
                    "audio_mix_error": "AUDIO_MIX_REQUEUE_FAILED",
                },
                update_modified=False,
            )
            result["failed"] += 1
            frappe.log_error(
                "Shorts operation failed.",
                "Short audio mix recovery failed",
            )

    frappe.logger("aos.shorts", allow_site=True).info(
        "shorts_audio_mix_recovery checked=%s requeued=%s failed=%s",
        result["checked"],
        result["requeued"],
        result["failed"],
    )
    return result

def maintain_short_integrity() -> dict[str, int]:
    """Bounded daily reconciliation for stale Shorts and derived counters.

    The scheduler owns no explicit commit; Frappe's job transaction remains the
    atomic boundary. Media removal is delegated to the canonical Media service.
    """
    from frappe.utils import add_to_date, now_datetime

    from aos.services.media.media_service import MediaService
    from aos.utils.aos_config import get_env_int

    limit = get_env_int("AOS_SHORTS_MAINTENANCE_BATCH_LIMIT", 250, min_value=10, max_value=1000)
    stale_hours = get_env_int("AOS_SHORTS_ABANDONED_UPLOAD_HOURS", 24, min_value=1, max_value=168)
    cutoff = add_to_date(now_datetime(), hours=-stale_hours)
    counters = {"abandoned": 0, "missing_jobs": 0, "released_media": 0, "reconciled": 0}

    abandoned = frappe.db.sql(
        """SELECT name, owner, raw_video_media, thumbnail_media
           FROM `tabAOS Short`
           WHERE status IN ('initialized','uploaded') AND modified < %s
             AND NOT EXISTS (
                SELECT 1 FROM `tabAOS Video Processing Job` j
                WHERE j.short=`tabAOS Short`.name
                  AND j.status IN ('Queued','Dispatching','Processing')
             )
           ORDER BY modified ASC, name ASC LIMIT %s FOR UPDATE""",
        (cutoff, limit),
        as_dict=True,
    )
    media_service = MediaService()
    for row in abandoned:
        frappe.db.set_value(
            "AOS Short",
            row.name,
            {"status": "failed", "processing_error": "UPLOAD_ABANDONED"},
            update_modified=False,
        )
        counters["abandoned"] += 1
        for media_id in {str(row.raw_video_media or ""), str(row.thumbnail_media or "")}:
            if not media_id:
                continue
            try:
                media_service.release_media(
                    media_id=media_id,
                    user=row.owner,
                    attached_doctype="AOS Short",
                    attached_name=row.name,
                    system=True,
                )
                counters["released_media"] += 1
            except Exception:
                frappe.log_error("Short maintenance media release failed.", "Shorts maintenance")

    missing_jobs = frappe.db.sql(
        """SELECT name FROM `tabAOS Short`
           WHERE status='processing' AND modified < %s
             AND NOT EXISTS (
                SELECT 1 FROM `tabAOS Video Processing Job` j
                WHERE j.short=`tabAOS Short`.name
                  AND j.status IN ('Queued','Dispatching','Processing')
             )
           ORDER BY modified ASC, name ASC LIMIT %s FOR UPDATE""",
        (add_to_date(now_datetime(), hours=-2), limit),
        pluck=True,
    )
    if missing_jobs:
        frappe.db.sql(
            """UPDATE `tabAOS Short`
               SET status='failed', processing_error='PROCESSING_JOB_MISSING'
               WHERE name IN %(names)s""",
            {"names": tuple(missing_jobs)},
        )
        counters["missing_jobs"] = len(missing_jobs)

    rows = frappe.db.sql(
        "SELECT name FROM `tabAOS Short` ORDER BY modified DESC, name DESC LIMIT %s FOR UPDATE",
        (limit,),
        as_dict=True,
    )
    for row in rows:
        frappe.db.sql(
            """UPDATE `tabAOS Short` s SET
                 like_count=(SELECT COUNT(*) FROM `tabAOS Short Like` l WHERE l.short=s.name),
                 save_count=(SELECT COUNT(*) FROM `tabAOS Short Save` v WHERE v.short=s.name),
                 comment_count=(SELECT COUNT(*) FROM `tabAOS Short Comment` c WHERE c.short=s.name AND c.status='active'),
                 repost_count=(SELECT COUNT(*) FROM `tabAOS Short Repost` r WHERE r.short=s.name AND r.status='active')
               WHERE s.name=%s""",
            (row.name,),
        )
        counters["reconciled"] += 1

    frappe.logger("aos.shorts", allow_site=True).info(
        "shorts_maintenance_complete abandoned=%s missing_jobs=%s released_media=%s reconciled=%s",
        counters["abandoned"], counters["missing_jobs"], counters["released_media"], counters["reconciled"],
    )
    return counters
