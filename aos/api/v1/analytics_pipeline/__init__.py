"""Public AOS API v1 wrappers for analytics_pipeline.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.analytics_pipeline.*.
Implementation stays in aos.api.analytics_pipeline implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.analytics_pipeline.callback import (
    handle_callback_impl as _handle_callback_impl,
)
from aos.api.analytics_pipeline.events import (
    track_event_impl as _track_event_impl,
    track_events_impl as _track_events_impl,
)

@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    """Execute the v1 analytics_pipeline.handle_callback endpoint."""
    return _handle_callback_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_event(**kwargs):
    """Execute the v1 analytics_pipeline.track_event endpoint."""
    return _track_event_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_events(**kwargs):
    """Execute the v1 analytics_pipeline.track_events endpoint."""
    return _track_events_impl(**kwargs)
