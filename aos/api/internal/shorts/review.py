"""Shorts staff transport projections; authorization remains feature-owned."""
from __future__ import annotations

import frappe

from aos.services.shorts.moderation import review_context, review_short


def get_review_context_impl(*, short_id: str):
    return {"ok": True, "data": review_context(short_id=short_id, reviewer=frappe.session.user)}


def review_impl(*, short_id: str, decision: str, reason: str = "", version: str = ""):
    doc = review_short(
        short_id=short_id, decision=decision, reason=reason, version=version,
        reviewer=frappe.session.user,
    )
    return {
        "ok": True,
        "data": {
            "short_id": doc.name,
            "lifecycle_status": doc.lifecycle_status,
            "moderation_status": doc.moderation_status,
            "version": str(doc.modified or ""),
        },
    }
