"""Content moderation callback API."""

from __future__ import annotations

import json

import frappe

from aos.api.shared.responses import fail, ok
from aos.services.moderation_service import (
    get_moderation_config,
    handle_moderation_callback,
    verify_signature,
)


def _request_body() -> bytes:
    try:
        if frappe.request:
            return frappe.request.get_data() or b""
    except Exception:
        pass
    return b""


def _payload_from_request(kwargs) -> dict:
    body = _request_body()
    if body:
        try:
            return json.loads(body.decode("utf-8") or "{}")
        except Exception:
            pass
    return dict(kwargs or {})


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    body = _request_body()
    try:
        signature = frappe.get_request_header("X-AOS-Moderation-Callback-Signature")
    except Exception:
        signature = None

    config = get_moderation_config()
    if body and not verify_signature(config.callback_secret, body, signature):
        return fail("Invalid moderation callback signature.", code="FORBIDDEN")

    try:
        payload = _payload_from_request(kwargs)
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
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "Moderation callback failed")
        return fail(str(exc) or "Moderation callback failed.", code="MODERATION_CALLBACK_FAILED")
