from __future__ import annotations

import frappe

from aos.api.shared.callback_security import CallbackSecurityError, read_signed_json_callback_payload
from aos.api.shared.responses import fail, ok
from aos.services.notification_delivery_service import (
    get_notification_delivery_config,
    handle_notification_delivery_callback,
    verify_signature,
)


SIGNATURE_HEADER = "X-AOS-Notification-Callback-Signature"


def _security_failure(exc: CallbackSecurityError):
    return fail(exc.message, code=exc.code, http_status=exc.http_status)


def handle_callback_impl(**kwargs):
    config = get_notification_delivery_config()
    try:
        payload = read_signed_json_callback_payload(
            callback_name="notification delivery",
            callback_secret=config.callback_secret,
            signature_header=SIGNATURE_HEADER,
            verify_signature=verify_signature,
        )
    except CallbackSecurityError as exc:
        return _security_failure(exc)

    try:
        job = handle_notification_delivery_callback(payload)
        return ok(
            "Notification delivery callback handled.",
            data={
                "job_id": job.name,
                "status": job.status,
                "success_count": job.success_count,
                "failure_count": job.failure_count,
                "inactive_count": job.inactive_count,
            },
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Notification delivery callback failed")
        return fail("Notification delivery callback failed.", code="CALLBACK_FAILED")
