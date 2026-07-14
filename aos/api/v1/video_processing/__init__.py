"""Public AOS API v1 wrappers for video_processing.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.video_processing.*.
Implementation stays in aos.api.video_processing implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.video_processing.callback import (
    handle_callback_impl as _handle_callback_impl,
)

@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    """Execute the v1 video_processing.handle_callback endpoint."""
    return _handle_callback_impl(**kwargs)
