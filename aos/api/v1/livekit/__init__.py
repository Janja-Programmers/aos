"""Signed LiveKit server callback route."""
from __future__ import annotations

import frappe

from aos.api.v1.livekit.webhook import handle_webhook_impl


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_webhook():
    return handle_webhook_impl()
