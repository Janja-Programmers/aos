"""Search/ranking callback implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.callback_security import CallbackSecurityError, read_signed_json_callback_payload
from aos.api.shared.responses import fail, ok
from aos.services.search_ranking_service import (
    get_search_ranking_config,
    handle_search_index_callback,
    verify_signature,
)


SIGNATURE_HEADER = "X-AOS-Search-Callback-Signature"


def _security_failure(exc: CallbackSecurityError):
    return fail(exc.message, code=exc.code, http_status=exc.http_status)


def handle_callback_impl(**kwargs):
    config = get_search_ranking_config()
    try:
        payload = read_signed_json_callback_payload(
            callback_name="search/ranking",
            callback_secret=config.callback_secret,
            signature_header=SIGNATURE_HEADER,
            verify_signature=verify_signature,
        )
    except CallbackSecurityError as exc:
        return _security_failure(exc)

    try:
        job = handle_search_index_callback(payload)
        return ok(
            "Search/ranking callback handled.",
            data={
                "job_id": job.name,
                "target_doctype": job.target_doctype,
                "target_name": job.target_name,
                "status": job.status,
                "action": job.action,
            },
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Search/ranking callback failed")
        return fail("Search/ranking callback failed.", code="SEARCH_RANKING_CALLBACK_FAILED")
