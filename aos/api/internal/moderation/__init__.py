"""Signed moderation callback and staff-only Desk routes."""
from __future__ import annotations

import frappe

from aos.api.moderation.callback import handle_callback_impl
from aos.api.internal.moderation.review import review_impl


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    return handle_callback_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def review(**kwargs):
    return review_impl(**kwargs)
