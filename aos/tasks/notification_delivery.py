from __future__ import annotations

import frappe

from aos.services.notification_delivery_service import (
    dispatch_notification_delivery_job as _dispatch_notification_delivery_job,
    retry_queued_notification_delivery_jobs as _retry_queued_notification_delivery_jobs,
)


def dispatch_notification_delivery_job(delivery_job_id: str | None = None, job_id: str | None = None):
    """Dispatch a persistent AOS Notification Delivery Job.

    Automatic Frappe queue calls must use `delivery_job_id` because `job_id` is a
    reserved enqueue option in Frappe/RQ.
    """
    resolved_job_id = delivery_job_id or job_id

    if not resolved_job_id:
        frappe.throw("Missing delivery_job_id for notification delivery dispatch.")

    return _dispatch_notification_delivery_job(resolved_job_id)


def retry_queued_notification_delivery_jobs():
    return _retry_queued_notification_delivery_jobs()
