from __future__ import annotations

import json

import frappe

from aos.api.shared.responses import fail, ok
from aos.services.analytics_pipeline_service import (
    get_analytics_pipeline_config,
    handle_analytics_ingest_callback,
    verify_signature,
)


def _get_request_body() -> bytes:
    try:
        return frappe.request.get_data() or b""
    except Exception:
        return b""


def _get_signature() -> str | None:
    try:
        return frappe.get_request_header("X-AOS-Analytics-Callback-Signature")
    except Exception:
        return None


def handle_callback_impl(**kwargs):
    try:
        body = _get_request_body()
        config = get_analytics_pipeline_config()
        if not verify_signature(config.callback_secret, body, _get_signature()):
            return fail("Invalid analytics callback signature.", code="UNAUTHORIZED")

        payload = json.loads(body.decode("utf-8") or "{}") if body else dict(kwargs or {})
        job = handle_analytics_ingest_callback(payload)
        return ok(
            "Analytics callback handled.",
            data={"job_id": job.name, "status": job.status},
        )
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "AOS analytics callback failed")
        return fail(str(exc) or "Failed to handle analytics callback", code="INTERNAL_ERROR")
