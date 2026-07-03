"""Video processing callback API."""

from __future__ import annotations

import json

import frappe

from aos.api.shared.responses import ok, fail
from aos.services.video_processing_service import (
    get_video_processing_config,
    handle_video_processing_callback,
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
    signature = None
    try:
        signature = frappe.get_request_header("X-AOS-Callback-Signature")
    except Exception:
        signature = None

    config = get_video_processing_config()
    if body and not verify_signature(config.callback_secret, body, signature):
        return fail("Invalid video callback signature.", code="FORBIDDEN")

    try:
        payload = _payload_from_request(kwargs)
        job = handle_video_processing_callback(payload)
        return ok(
            "Video processing callback handled.",
            data={
                "job_id": job.name,
                "short_id": job.short,
                "status": job.status,
            },
        )
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "Video processing callback failed")
        return fail(str(exc) or "Video callback failed.", code="VIDEO_CALLBACK_FAILED")
