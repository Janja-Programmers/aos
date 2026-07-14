"""Public AOS API v1 wrappers for notification_delivery.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.notification_delivery.*.
Implementation stays in aos.api.notification_delivery implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.notification_delivery.callback import (
    handle_callback_impl as _handle_callback_impl,
)

@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    """Execute the v1 notification_delivery.handle_callback endpoint."""
    return _handle_callback_impl(**kwargs)
