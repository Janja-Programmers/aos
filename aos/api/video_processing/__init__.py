"""Video processing API wrappers."""

from __future__ import annotations

import frappe

from .callback import handle_callback_impl


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    return handle_callback_impl(**kwargs)
