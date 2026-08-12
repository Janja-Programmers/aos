"""Public AOS API v1 wrappers for private Activity Center history."""

from __future__ import annotations

import frappe

from aos.api.shared.transport import client_kwargs
from aos.api.activity.activity import (
    clear_activity_impl as _clear_activity_impl,
    hide_activity_impl as _hide_activity_impl,
    list_activity_impl as _list_activity_impl,
)


@frappe.whitelist(methods=["GET"])
def list_activity(**kwargs):
    """List current user's private Activity Center rows."""
    return _list_activity_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def hide_activity(**kwargs):
    """Hide one Activity Center item."""
    return _hide_activity_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def clear_activity(**kwargs):
    """Clear Activity Center items, optionally filtered by group/type."""
    return _clear_activity_impl(**client_kwargs(kwargs))
