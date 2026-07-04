"""Search/ranking API wrappers."""

from __future__ import annotations

import frappe

from .callback import handle_callback_impl
from .recommendations import related_ads_impl


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    return handle_callback_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def related_ads(**kwargs):
    return related_ads_impl(**kwargs)
