"""Signed callback boundary for the private Notifications delivery companion."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.callback_security import CallbackSecurityError, read_signed_json_callback_payload
from aos.api.shared.callback_transaction import execute_callback_atomically, extract_callback_conflict_code
from aos.api.shared.public_errors import safe_exception_message
from aos.api.shared.responses import fail, ok
from aos.services.notifications.delivery import (
    NotificationDeliveryError,
    get_notification_delivery_config,
    handle_notification_delivery_callback,
    verify_signature,
)

SIGNATURE_HEADER = "X-AOS-Notification-Callback-Signature"
_CALLBACK_FIELDS = frozenset(
    {
        "job_id",
        "idempotency_key",
        "dispatch_id",
        "dispatch_generation",
        "dispatch_token",
        "service_job_id",
        "delivery_identity",
        "status",
        "channel",
        "token_count",
        "success_count",
        "failure_count",
        "inactive_token_hashes",
        "provider_responses",
        "error",
        "message",
        "provider_resolution",
    }
)


def _security_failure(exc: CallbackSecurityError):
    error = "DELIVERY_CALLBACK_INVALID" if exc.error == "VALIDATION_ERROR" else exc.error
    return fail(
        safe_exception_message(exc, "Callback authentication failed."),
        error=error,
        http_status=exc.http_status,
    )


def _validate_callback_shape(payload: dict[str, Any]) -> None:
    unknown = sorted(set(payload) - _CALLBACK_FIELDS)
    if unknown:
        raise NotificationDeliveryError("Unsupported notification delivery callback field")
    for field in ("job_id", "idempotency_key", "dispatch_id", "dispatch_token", "status"):
        if not str(payload.get(field) or "").strip():
            raise NotificationDeliveryError(f"{field} is required")
    try:
        generation = int(payload.get("dispatch_generation"))
    except (TypeError, ValueError) as exc:
        raise NotificationDeliveryError("dispatch_generation is invalid") from exc
    if generation < 0 or generation > 1_000_000:
        raise NotificationDeliveryError("dispatch_generation is invalid")
    if str(payload.get("channel") or "push").strip().lower() != "push":
        raise NotificationDeliveryError("Unsupported notification delivery channel")
    if not isinstance(payload.get("inactive_token_hashes", []), list):
        raise NotificationDeliveryError("inactive_token_hashes must be an array")
    if not isinstance(payload.get("provider_responses", []), list):
        raise NotificationDeliveryError("provider_responses must be an array")


def handle_delivery_callback_impl(**kwargs):
    # The signed raw JSON body is authoritative. ``execute_endpoint`` has
    # already stripped framework-owned transport metadata; body fields are
    # validated below rather than trusted from Frappe kwargs/form parsing.
    del kwargs
    config = get_notification_delivery_config()
    try:
        payload = read_signed_json_callback_payload(
            callback_name="notification delivery",
            callback_secret=config.callback_secret,
            signature_header=SIGNATURE_HEADER,
            verify_signature=verify_signature,
        )
        _validate_callback_shape(payload)
    except CallbackSecurityError as exc:
        return _security_failure(exc)
    except NotificationDeliveryError as exc:
        return fail(
            safe_exception_message(exc, "Invalid delivery callback."),
            error="DELIVERY_CALLBACK_INVALID",
            http_status=400,
        )

    try:
        job = execute_callback_atomically(
            service_type="notification_delivery",
            job_doctype="AOS Notification Delivery Job",
            job_name=str(payload.get("job_id") or "").strip(),
            operation=lambda: handle_notification_delivery_callback(payload),
        )
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
    except NotificationDeliveryError as exc:
        return fail(
            safe_exception_message(exc, "Invalid delivery callback."),
            error="DELIVERY_CALLBACK_INVALID",
            http_status=400,
        )
    except Exception as exc:
        conflict_code = extract_callback_conflict_code(exc)
        if conflict_code:
            frappe.logger("aos.callbacks", allow_site=True).warning(
                "Signed notification delivery callback rejected: category=%s", conflict_code
            )
            return fail("Callback state conflict.", error=conflict_code, http_status=409)
        frappe.log_error(frappe.get_traceback(), "Notifications delivery callback failed")
        return fail("Callback processing failed.", error="INTERNAL_ERROR", http_status=500)
