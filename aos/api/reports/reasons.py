"""List active report reasons."""

from __future__ import annotations

import frappe

from aos.api.shared.responses import ok, fail


def list_report_reasons_impl(**kwargs):
    try:
        reasons = frappe.get_all(
            "AOS Report Reason",
            filters={"is_active": 1},
            fields=[
                "reason",
                "icon_key",
                "sort_order"
            ],
            order_by="sort_order asc"
        )

        items = []

        for r in reasons:
            items.append({
                "id": r.reason,
                "reason": r.reason,
                "icon_key": r.icon_key
            })

        return ok(
            "Report reasons fetched.",
            data={"items": items}
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS List Report Reasons Failed")
        return fail("Failed to fetch reasons.", code="INTERNAL_ERROR")
