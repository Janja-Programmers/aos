from __future__ import annotations

import frappe

from aos.services.video_processing_service import dispatch_video_processing_job as _dispatch_video_processing_job


def dispatch_video_processing_job(video_job_id: str | None = None, job_id: str | None = None):
    """Dispatch a persistent AOS Video Processing Job to the video service.

    Automatic Frappe queue calls must use `video_job_id` because `job_id` is a
    reserved enqueue option in Frappe/RQ. `job_id` is retained only as a
    defensive fallback for manual console calls.
    """
    resolved_job_id = video_job_id or job_id
    if not resolved_job_id:
        frappe.throw("Missing video_job_id for video processing dispatch.")

    try:
        return _dispatch_video_processing_job(resolved_job_id)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Video processing dispatch failed: {resolved_job_id}",
        )
        raise


def dispatch_video_processing_job_task(video_job_id: str | None = None, job_id: str | None = None):
    """Alias for manual/retry use."""
    return dispatch_video_processing_job(video_job_id=video_job_id, job_id=job_id)
