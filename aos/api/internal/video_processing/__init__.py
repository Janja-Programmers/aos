"""Signed internal callback boundary for the private Video Processing companion.

This namespace is intentionally excluded from the client/Postman contract.
"""
from __future__ import annotations

import frappe

from aos.services.video_processing_callback import handle_callback_impl


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    return handle_callback_impl(**kwargs)
