"""Public AOS API v1 wrappers for activity.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.activity.*.
Implementation stays in aos.api.activity implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.activity.activity import (
    list_activity_impl as _list_activity_impl,
    hide_activity_impl as _hide_activity_impl,
    clear_activity_impl as _clear_activity_impl,
)

@frappe.whitelist(methods=["GET"])
def list_activity(**kwargs):
    """List current user's private Activity Center rows."""
    return _list_activity_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def hide_activity(**kwargs):
    """Hide one Activity Center item."""
    return _hide_activity_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def clear_activity(**kwargs):
    """Clear Activity Center items, optionally filtered by group/type."""
    return _clear_activity_impl(**kwargs)
