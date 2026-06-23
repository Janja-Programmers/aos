"""Activity Center endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .activity import (
    list_activity_impl,
    hide_activity_impl,
    clear_activity_impl,
)


@frappe.whitelist(methods=["GET"])
def list_activity(**kwargs):
    """List current user's private Activity Center rows."""
    return list_activity_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def hide_activity(**kwargs):
    """Hide one Activity Center item."""
    return hide_activity_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def clear_activity(**kwargs):
    """Clear Activity Center items, optionally filtered by group/type."""
    return clear_activity_impl(**kwargs)
