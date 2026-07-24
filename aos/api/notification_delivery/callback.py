from __future__ import annotations

import frappe

from aos.api.shared.callback_security import CallbackSecurityError, read_signed_json_callback_payload
from aos.api.shared.callback_transaction import (
	execute_callback_atomically,
	extract_callback_conflict_code,
)
from aos.api.shared.public_errors import safe_exception_message
from aos.api.shared.responses import fail, ok
from aos.services.notification_delivery_service import (
	get_notification_delivery_config,
	handle_notification_delivery_callback,
	verify_signature,
)

SIGNATURE_HEADER = "X-AOS-Notification-Callback-Signature"


def _security_failure(exc: CallbackSecurityError):
	return fail(
		safe_exception_message(exc, "Callback authentication failed."),
		error=exc.error,
		http_status=exc.http_status,
	)


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
	except Exception as exc:
		conflict_code = extract_callback_conflict_code(exc)
		if conflict_code:
			frappe.logger("aos.callbacks", allow_site=True).warning(
				"Signed notification delivery callback rejected: category=%s", conflict_code
			)
			return fail("Callback state conflict.", error=conflict_code, http_status=409)
		frappe.log_error(frappe.get_traceback(), "Notification Delivery callback failed")
		return fail("Callback processing failed.", error="CALLBACK_FAILED")
