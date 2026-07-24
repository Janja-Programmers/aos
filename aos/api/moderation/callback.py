"""Content moderation callback implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.callback_security import CallbackSecurityError, read_signed_json_callback_payload
from aos.api.shared.callback_transaction import (
	execute_callback_atomically,
	extract_callback_conflict_code,
)
from aos.api.shared.public_errors import safe_exception_message
from aos.api.shared.responses import fail, ok
from aos.services.moderation_service import (
	get_moderation_config,
	handle_moderation_callback,
	verify_signature,
)

SIGNATURE_HEADER = "X-AOS-Moderation-Callback-Signature"


def _security_failure(exc: CallbackSecurityError):
	return fail(
		safe_exception_message(exc, "Callback authentication failed."),
		error=exc.error,
		http_status=exc.http_status,
	)


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
		job = execute_callback_atomically(
			service_type="moderation",
			job_doctype="AOS Moderation Job",
			job_name=str(payload.get("job_id") or "").strip(),
			operation=lambda: handle_moderation_callback(payload),
		)
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
		conflict_code = extract_callback_conflict_code(exc)
		if conflict_code:
			frappe.logger("aos.callbacks", allow_site=True).warning(
				"Signed moderation callback rejected: category=%s", conflict_code
			)
			return fail("Callback state conflict.", error=conflict_code, http_status=409)
		frappe.log_error(frappe.get_traceback(), "Moderation callback failed")
		return fail("Callback processing failed.", error="MODERATION_CALLBACK_FAILED")
