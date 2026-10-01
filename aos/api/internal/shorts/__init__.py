"""Desk-only Shorts routes; no public client endpoint."""
from __future__ import annotations

import frappe

from aos.api.internal.shorts.review import get_review_context_impl, review_impl


@frappe.whitelist(methods=["GET"])
def get_review_context(short_id: str):
    return get_review_context_impl(short_id=short_id)


@frappe.whitelist(methods=["POST"])
def review(short_id: str, decision: str, reason: str = "", version: str = ""):
    return review_impl(short_id=short_id, decision=decision, reason=reason, version=version)
