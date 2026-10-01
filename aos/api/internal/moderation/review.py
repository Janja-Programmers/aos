"""Desk moderation review adapter; no public client endpoint."""
from __future__ import annotations

import frappe

from aos.services.moderation.manual_review import review_moderation_case


def review_impl(**kwargs):
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
