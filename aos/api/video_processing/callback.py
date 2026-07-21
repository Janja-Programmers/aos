"""Video processing callback implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.callback_security import CallbackSecurityError, read_signed_json_callback_payload
from aos.api.shared.callback_transaction import execute_callback_atomically
from aos.api.shared.public_errors import safe_exception_message
from aos.api.shared.responses import fail, ok
from aos.services.transactional_outbox import OutboxConflictError
from aos.services.video_processing_service import (
	get_video_processing_config,
	handle_video_processing_callback,
	verify_signature,
)

SIGNATURE_HEADER = "X-AOS-Callback-Signature"


def _security_failure(exc: CallbackSecurityError):
	return fail(
		safe_exception_message(exc, "Callback authentication failed."),
		error=exc.error,
		http_status=exc.http_status,
	)


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
		job = execute_callback_atomically(
			service_type="video_processing",
			job_doctype="AOS Video Processing Job",
			job_name=str(payload.get("job_id") or "").strip(),
			operation=lambda: handle_video_processing_callback(payload),
		)
		return ok(
			"Video processing callback handled.",
			data={
				"job_id": job.name,
				"short_id": job.short,
				"status": job.status,
			},
		)
	except OutboxConflictError as exc:
		frappe.logger("aos.callbacks", allow_site=True).warning(
			"Signed video processing callback rejected: category=%s", exc.error_code
		)
		return fail("Callback state conflict.", error=exc.error_code, http_status=409)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Video Processing callback failed")
		return fail("Callback processing failed.", error="VIDEO_CALLBACK_FAILED")
