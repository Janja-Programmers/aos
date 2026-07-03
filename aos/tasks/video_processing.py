from __future__ import annotations

import frappe

from aos.services.video_processing_service import dispatch_video_processing_job as _dispatch_video_processing_job


def dispatch_video_processing_job(job_id: str):
    """Dispatch a persistent AOS Video Processing Job to the video service."""
    if not job_id:
        return
    try:
        return _dispatch_video_processing_job(job_id)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Video processing dispatch failed: {job_id}",
        )
        raise


def dispatch_video_processing_job_task(job_id: str):
    """Alias for manual/retry use."""
    return dispatch_video_processing_job(job_id)
