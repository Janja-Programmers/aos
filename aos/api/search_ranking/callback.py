"""Search/ranking callback implementation."""

from __future__ import annotations

import json

import frappe

from aos.api.shared.responses import fail, ok
from aos.services.search_ranking_service import (
    get_search_ranking_config,
    handle_search_index_callback,
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


def handle_callback_impl(**kwargs):
    body = _request_body()
    try:
        signature = frappe.get_request_header("X-AOS-Search-Callback-Signature")
    except Exception:
        signature = None

    config = get_search_ranking_config()
    if body and not verify_signature(config.callback_secret, body, signature):
        return fail("Invalid search/ranking callback signature.", code="FORBIDDEN")

    try:
        payload = _payload_from_request(kwargs)
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
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "Search/ranking callback failed")
        return fail(str(exc) or "Search/ranking callback failed.", code="SEARCH_RANKING_CALLBACK_FAILED")
