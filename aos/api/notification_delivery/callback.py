from __future__ import annotations

import json

import frappe

from aos.api.shared.responses import fail, ok
from aos.services.notification_delivery_service import (
    get_notification_delivery_config,
    handle_notification_delivery_callback,
    verify_signature,
)


def _read_payload() -> tuple[dict, bytes]:
    raw = frappe.request.get_data() if getattr(frappe, "request", None) else b""
    if not raw:
        raw = json.dumps(frappe.form_dict or {}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        payload = dict(frappe.form_dict or {})
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return payload, raw


def handle_callback_impl(**kwargs):
    payload, raw = _read_payload()
    config = get_notification_delivery_config()
    signature = None
    try:
        signature = frappe.get_request_header("X-AOS-Notification-Callback-Signature")
    except Exception:
        signature = None

    if not verify_signature(config.callback_secret, raw, signature):
        return fail("Invalid notification delivery callback signature.", code="UNAUTHORIZED")

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
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "Notification delivery callback failed")
        return fail(str(exc) or "Notification delivery callback failed.", code="CALLBACK_FAILED")
