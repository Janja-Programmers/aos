"""Video processing callback implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.callback_security import CallbackSecurityError, read_signed_json_callback_payload
from aos.api.shared.responses import fail, ok
from aos.services.video_processing_service import (
    get_video_processing_config,
    handle_video_processing_callback,
    verify_signature,
)


SIGNATURE_HEADER = "X-AOS-Callback-Signature"


def _security_failure(exc: CallbackSecurityError):
    return fail(exc.message, code=exc.code, http_status=exc.http_status)


def handle_callback_impl(**kwargs):
    config = get_video_processing_config()
    try:
        payload = read_signed_json_callback_payload(
            callback_name="video processing",
            callback_secret=config.callback_secret,
            signature_header=SIGNATURE_HEADER,
            verify_signature=verify_signature,
        )
    except CallbackSecurityError as exc:
        return _security_failure(exc)

    try:
        job = handle_video_processing_callback(payload)
        return ok(
            "Video processing callback handled.",
            data={
                "job_id": job.name,
                "short_id": job.short,
                "status": job.status,
            },
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Video processing callback failed")
        return fail("Video processing callback failed.", code="VIDEO_CALLBACK_FAILED")
