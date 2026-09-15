"""Internal Search Ranking callback API.

Candidate search itself is consumed by Frappe service code. This signed callback
is not a frontend/Postman contract.
"""
from __future__ import annotations
import frappe
from aos.api.shared.transport import execute_endpoint
from aos.api.search_ranking.callback import handle_callback_impl

@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    return execute_endpoint(handle_callback_impl, kwargs)
