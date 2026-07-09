from __future__ import annotations

import frappe

from aos.api.shared.callback_security import CallbackSecurityError, read_signed_json_callback_payload
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_exception_message
from aos.services.analytics_pipeline_service import (
    get_analytics_pipeline_config,
    handle_analytics_ingest_callback,
    verify_signature,
)


SIGNATURE_HEADER = "X-AOS-Analytics-Callback-Signature"


def _security_failure(exc: CallbackSecurityError):
    return fail(
        safe_exception_message(exc, "Callback authentication failed."),
        error=exc.error,
        http_status=exc.http_status,
    )


def handle_callback_impl(**kwargs):
    config = get_analytics_pipeline_config()
    try:
        payload = read_signed_json_callback_payload(
            callback_name="analytics pipeline",
            callback_secret=config.callback_secret,
            signature_header=SIGNATURE_HEADER,
            verify_signature=verify_signature,
        )
    except CallbackSecurityError as exc:
        return _security_failure(exc)

    try:
        job = handle_analytics_ingest_callback(payload)
        return ok(
            "Analytics callback handled.",
            data={"job_id": job.name, "status": job.status},
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS analytics callback failed")
        return fail("Failed to handle analytics callback.", error="ANALYTICS_CALLBACK_FAILED")
