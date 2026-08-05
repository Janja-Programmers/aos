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
    """Repair missing or stale durable sound-processing work.

    Selected-sound Shorts can be left either ``ready`` with a pending remix or
    ``processing`` when an older initial/callback generation never completed.
    Recovery therefore handles both states. It never reuses a dead-lettered
    companion callback token: stale jobs and outbox rows are made terminal and
    a fresh durable generation is created atomically.

    A processing Short that already has playable output is restored to ``ready``
    before starting an audio-only generation. A processing Short without base
    output receives a normal full retry so thumbnail and classification work are
    not skipped. ``stale_minutes=0`` is an explicit operator override.
    """
    from frappe.utils import add_to_date, now_datetime

    from aos.services.shorts.repository import ShortsRepository
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
    bounded_limit = max(1, min(batch_limit, 500))

    # Only selected-sound Shorts are remix candidates. Historical rows created
    # before audio lifecycle hardening may have ``audio_mix_status=processing``
    # without any selected sound; those rows are normalized separately below.
    rows = frappe.db.sql(
        """
        SELECT
            s.name,
            s.status,
            s.audio_mix_status,
            s.processed_file_key,
            s.processed_file_url,
            s.playback_url,
            s.thumbnail_media,
            s.thumbnail_url
        FROM `tabAOS Short` s
        WHERE s.status IN ('ready', 'processing')
          AND s.audio_mix_status IN ('none', 'pending', 'processing', 'failed')
          AND EXISTS (
              SELECT 1
              FROM `tabAOS Short Sound` ss
              WHERE ss.short = s.name
                AND ss.is_original_audio = 0
          )
        ORDER BY s.modified ASC, s.name ASC
        LIMIT %s
        """,
        (bounded_limit,),
        as_dict=True,
    )

    result = {
        "checked": len(rows),
        "requeued": 0,
        "audio_requeues": 0,
        "full_retries": 0,
        "restored_ready": 0,
        "cancelled_stale": 0,
        "skipped_active": 0,
        "normalized_without_sound": 0,
        "failed": 0,
    }
    repository = ShortsRepository()

    for index, row in enumerate(rows):
        savepoint = f"aos_audio_mix_recovery_{index}"
        frappe.db.savepoint(savepoint)
        try:
            locked_short, active_jobs = repository.lock_short_and_active_jobs(row.name)
            if not locked_short:
                continue

            current = frappe.db.get_value(
                "AOS Short",
                row.name,
                [
                    "status",
                    "audio_mix_status",
                    "processed_file_key",
                    "processed_file_url",
                    "playback_url",
                    "thumbnail_media",
                    "thumbnail_url",
                ],
                as_dict=True,
            )
            if not current or current.status not in {"ready", "processing"}:
                continue
            if current.audio_mix_status not in {"none", "pending", "processing", "failed"}:
                continue

            has_selected_sound = bool(
                frappe.db.exists(
                    "AOS Short Sound",
                    {"short": row.name, "is_original_audio": 0},
                )
            )
            if not has_selected_sound:
                frappe.db.set_value(
                    "AOS Short",
                    row.name,
                    {"audio_mix_status": "none", "audio_mix_error": None},
                    update_modified=False,
                )
                result["normalized_without_sound"] += 1
                continue

            fresh_active = [
                job
                for job in active_jobs
                if job.get("modified") and job.get("modified") > cutoff
            ]
            if fresh_active:
                result["skipped_active"] += 1
                continue

            stale_job_names = tuple(
                str(job.get("name") or "")
                for job in active_jobs
                if job.get("name")
            )
            if stale_job_names:
                frappe.db.sql(
                    """
                    UPDATE `tabAOS Video Processing Job`
                    SET status = 'Cancelled',
                        active_key = NULL,
                        completed_at = NOW(),
                        last_error = 'AUDIO_MIX_RECOVERY_STALE'
                    WHERE name IN %(names)s
                      AND status IN ('Queued', 'Dispatching', 'Processing')
                    """,
                    {"names": stale_job_names},
                )
                if frappe.db.table_exists("AOS Transactional Outbox"):
                    frappe.db.sql(
                        """
                        UPDATE `tabAOS Transactional Outbox`
                        SET status = 'Cancelled',
                            completed_at = NOW(),
                            next_attempt_at = NULL,
                            callback_deadline_at = NULL,
                            claimed_by = NULL,
                            claim_token = NULL,
                            claimed_at = NULL,
                            lease_expires_at = NULL,
                            last_error = 'AUDIO_MIX_RECOVERY_STALE'
                        WHERE job_doctype = 'AOS Video Processing Job'
                          AND job_name IN %(names)s
                          AND status NOT IN (
                              'Completed', 'Completed With Failure',
                              'Dead Letter', 'Cancelled'
                          )
                        """,
                        {"names": stale_job_names},
                    )
                result["cancelled_stale"] += len(stale_job_names)

            has_base_output = bool(
                current.processed_file_key
                and current.processed_file_url
                and current.playback_url
                and (current.thumbnail_media or current.thumbnail_url)
            )
            if has_base_output:
                if current.status != "ready":
                    frappe.db.set_value(
                        "AOS Short",
                        row.name,
                        {
                            "status": "ready",
                            "processing_error": None,
                            "audio_mix_status": "pending",
                            "audio_mix_error": None,
                        },
                        update_modified=False,
                    )
                    result["restored_ready"] += 1
                create_video_processing_job(
                    short_id=row.name,
                    force=True,
                    reason="audio_reprocess",
                    enqueue=True,
                )
                result["audio_requeues"] += 1
            else:
                # No canonical base output exists, so this is a stale initial
                # generation rather than a pure remix. A full retry is required
                # to recreate thumbnail and classification metadata.
                frappe.db.set_value(
                    "AOS Short",
                    row.name,
                    {
                        "status": "failed",
                        "processing_error": "PROCESSING_RECOVERY_RETRY",
                        "audio_mix_status": "processing",
                        "audio_mix_error": None,
                    },
                    update_modified=False,
                )
                create_video_processing_job(
                    short_id=row.name,
                    force=False,
                    reason="retry",
                    enqueue=True,
                )
                result["full_retries"] += 1

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

    # Normalize a bounded set of historical false-positive audio states. Initial
    # video processing used to set ``audio_mix_status=processing`` even when no
    # selected sound existed. These rows are not remix work and must not remain
    # visible as indefinitely pending audio.
    without_sound = frappe.db.sql(
        """
        SELECT s.name
        FROM `tabAOS Short` s
        WHERE s.status IN ('ready', 'processing', 'failed')
          AND s.audio_mix_status IN ('pending', 'processing', 'failed')
          AND NOT EXISTS (
              SELECT 1
              FROM `tabAOS Short Sound` ss
              WHERE ss.short = s.name
                AND ss.is_original_audio = 0
          )
        ORDER BY s.modified ASC, s.name ASC
        LIMIT %s
        """,
        (bounded_limit,),
        as_dict=True,
    )
    for row in without_sound:
        frappe.db.set_value(
            "AOS Short",
            row.name,
            {"audio_mix_status": "none", "audio_mix_error": None},
            update_modified=False,
        )
        result["normalized_without_sound"] += 1

    frappe.logger("aos.shorts", allow_site=True).info(
        "shorts_audio_mix_recovery checked=%s requeued=%s audio_requeues=%s "
        "full_retries=%s restored_ready=%s cancelled_stale=%s skipped_active=%s "
        "normalized_without_sound=%s failed=%s",
        result["checked"],
        result["requeued"],
        result["audio_requeues"],
        result["full_retries"],
        result["restored_ready"],
        result["cancelled_stale"],
        result["skipped_active"],
        result["normalized_without_sound"],
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
