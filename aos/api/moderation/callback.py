"""Content moderation callback implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.callback_security import CallbackSecurityError, read_signed_json_callback_payload
from aos.api.shared.responses import fail, ok
from aos.services.moderation_service import (
    get_moderation_config,
    handle_moderation_callback,
    verify_signature,
)


SIGNATURE_HEADER = "X-AOS-Moderation-Callback-Signature"


def _security_failure(exc: CallbackSecurityError):
    return fail(exc.message, code=exc.code, http_status=exc.http_status)


def handle_callback_impl(**kwargs):
    config = get_moderation_config()
    try:
        payload = read_signed_json_callback_payload(
            callback_name="moderation",
            callback_secret=config.callback_secret,
            signature_header=SIGNATURE_HEADER,
            verify_signature=verify_signature,
        )
    except CallbackSecurityError as exc:
        return _security_failure(exc)

    try:
        job = handle_moderation_callback(payload)
        return ok(
            "Moderation callback handled.",
            data={
                "job_id": job.name,
                "target_doctype": job.target_doctype,
                "target_name": job.target_name,
                "status": job.status,
                "decision": job.decision,
            },
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Moderation callback failed")
        return fail("Moderation callback failed.", code="MODERATION_CALLBACK_FAILED", http_status=500)
