"""Public AOS API v1 wrappers for moderation.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.moderation.*.
Implementation stays in aos.api.moderation implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.moderation.callback import (
    handle_callback_impl as _handle_callback_impl,
)

@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    """Execute the v1 moderation.handle_callback endpoint."""
    return _handle_callback_impl(**kwargs)
