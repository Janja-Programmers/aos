"""Public AOS API v1 wrappers for analytics_pipeline.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.analytics_pipeline.*.
Implementation stays in aos.api.analytics_pipeline implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.analytics_pipeline.callback import (
    handle_callback_impl as _handle_callback_impl,
)

@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    """Execute the v1 analytics_pipeline.handle_callback endpoint."""
    return _handle_callback_impl(**kwargs)

