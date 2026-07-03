from __future__ import annotations

import frappe

from aos.services.moderation_service import dispatch_moderation_job as _dispatch_moderation_job


def dispatch_moderation_job(moderation_job_id: str | None = None, job_id: str | None = None):
    """Dispatch a persistent AOS Moderation Job to the moderation service.

    Automatic Frappe queue calls must use `moderation_job_id` because `job_id`
    is a reserved enqueue option in Frappe/RQ.
    """
    resolved_job_id = moderation_job_id or job_id
    if not resolved_job_id:
        frappe.throw("Missing moderation_job_id for moderation dispatch.")
    return _dispatch_moderation_job(resolved_job_id)
