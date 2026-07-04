"""Search/ranking callback API."""

from __future__ import annotations

import json

import frappe

from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import require_id
from aos.api.ads.serializers import serialize_ad_list_item
from aos.services.search_ranking_service import related_ad_candidates
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


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
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


@frappe.whitelist(allow_guest=True, methods=["GET"])
def related_ads(**kwargs):
    """Return related active ads using search-ranking candidates and Frappe hydration."""
    ad_id, err = require_id(kwargs.get("ad_id") or kwargs.get("id"), "ad_id")
    if err:
        return err
    try:
        limit = max(1, min(int(kwargs.get("limit") or 20), 50))
    except Exception:
        limit = 20
    try:
        candidate_ids = related_ad_candidates(ad_id=ad_id, limit=limit)
        if not candidate_ids:
            return ok("Related ads fetched.", data={"items": []})
        docs = []
        for candidate_id in candidate_ids:
            if not frappe.db.exists("AOS Ad", candidate_id):
                continue
            doc = frappe.get_doc("AOS Ad", candidate_id)
            if doc.status != "Active":
                continue
            docs.append(doc)
        return ok("Related ads fetched.", data={"items": [serialize_ad_list_item(doc) for doc in docs]})
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Related ads search/ranking failed")
        return fail("Failed to fetch related ads.", code="INTERNAL_ERROR")
