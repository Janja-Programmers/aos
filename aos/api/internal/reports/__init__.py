"""Desk-only Reports routes; report decisions are manual and staff-authorized."""
from __future__ import annotations

import frappe

from aos.api.internal.reports.review import review_impl


@frappe.whitelist(methods=["POST"])
def review(**kwargs):
    return review_impl(**kwargs)
