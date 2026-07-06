"""Analytics pipeline API endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .callback import handle_callback_impl
from .events import track_event_impl, track_events_impl


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    return handle_callback_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_event(**kwargs):
    return track_event_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_events(**kwargs):
    return track_events_impl(**kwargs)
