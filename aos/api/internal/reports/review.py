"""Desk Report review adapter; no public client endpoint."""
from __future__ import annotations

import frappe

from aos.services.reports.manual_review import review_report


def review_impl(**kwargs):
    data = review_report(
        report_type=kwargs.get("report_type"),
        report_id=kwargs.get("report_id"),
        decision=kwargs.get("decision"),
        note=kwargs.get("note"),
        version=kwargs.get("version"),
        reviewer=frappe.session.user,
    )
    return {"ok": True, "data": data}
