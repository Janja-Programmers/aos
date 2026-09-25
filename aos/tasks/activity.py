"""Bounded scheduled maintenance for the Activity read model."""

from __future__ import annotations

from aos.services.activity.constants import RETENTION_DELETE_BATCH_SIZE
from aos.services.activity.observability import activity_log
from aos.services.activity_service import ActivityService

_MAX_BATCHES_PER_RUN = 10


def cleanup_activity_retention() -> dict[str, int]:
    """Delete at most 50k expired Activity rows per five-minute scheduler run.

    Retention is eventual maintenance only; Activity correctness never depends
    on this job running on one specific worker or node.
    """
    deleted = 0
    batches = 0
    for _ in range(_MAX_BATCHES_PER_RUN):
        removed = ActivityService.purge_expired(limit=RETENTION_DELETE_BATCH_SIZE)
        if not removed:
            break
        deleted += removed
        batches += 1
        if removed < RETENTION_DELETE_BATCH_SIZE:
            break
    activity_log("activity.retention", count=deleted)
    return {"deleted": deleted, "batches": batches}
