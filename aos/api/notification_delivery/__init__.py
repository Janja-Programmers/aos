"""Notification delivery service callback endpoint.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .callback import handle_callback_impl


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    return handle_callback_impl(**kwargs)
