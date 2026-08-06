"""Signed LiveKit server callbacks."""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail, ok
from aos.services.live.webhooks import MAX_WEBHOOK_BYTES, handle_verified_webhook


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_webhook():
    request = getattr(frappe, "request", None)
    if request is None:
        return fail("Invalid webhook request.", error="LIVE_WEBHOOK_INVALID", http_status=401)
    content_length = request.content_length
    if content_length is not None and int(content_length) > MAX_WEBHOOK_BYTES:
        return fail(
            "LiveKit webhook rejected.",
            error="LIVE_WEBHOOK_INVALID",
            http_status=413,
        )
    raw_body = request.get_data(cache=True, as_text=True)
    authorization = str(request.headers.get("Authorization") or "")
    result = handle_verified_webhook(raw_body, authorization)
    if not result.get("ok"):
        code = str(result.get("error") or "LIVE_WEBHOOK_INVALID")
        status = 401 if code == "LIVE_WEBHOOK_INVALID" else 503 if code == "LIVE_WEBHOOK_RETRY" else 500
        return fail("LiveKit webhook rejected.", error=code, http_status=status)
    return ok(
        "LiveKit webhook processed.",
        data={"duplicate": bool(result.get("duplicate")), "outcome": result.get("outcome")},
    )
