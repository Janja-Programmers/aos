"""Signed service callback and staff-only Desk actions for Moderation.

This namespace is intentionally not part of the public AOS v1 client contract.
"""
from __future__ import annotations

import frappe

from aos.api.moderation.callback import handle_callback_impl
from aos.services.moderation.manual_review import review_moderation_case


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    """Receive the HMAC/timestamp authenticated moderation-service callback."""
    return handle_callback_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def review(**kwargs):
    """Apply one authorized human decision from Frappe Desk."""
    job = review_moderation_case(
        job_id=kwargs.get("job_id"),
        decision=kwargs.get("decision"),
        reason=kwargs.get("reason"),
        version=kwargs.get("version"),
        reviewer=frappe.session.user,
    )
    return {
        "ok": True,
        "data": {
            "job_id": job.name,
            "status": job.status,
            "decision": job.decision,
            "decision_source": job.decision_source,
            "version": str(job.modified or ""),
        },
    }
