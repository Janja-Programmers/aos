"""Public AOS API v1 wrappers for notifications.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.notifications.*.
Implementation stays in aos.api.notifications implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.v1._transport import client_kwargs as _client_kwargs

from aos.api.notifications.token import (
    register_push_token_impl as _register_push_token_impl,
    deactivate_push_token_impl as _deactivate_push_token_impl,
)
from aos.api.notifications.notification import (
    list_notifications_impl as _list_notifications_impl,
    mark_notification_read_impl as _mark_notification_read_impl,
    mark_all_notifications_read_impl as _mark_all_notifications_read_impl,
    delete_notification_impl as _delete_notification_impl,
    clear_notifications_impl as _clear_notifications_impl,
)

@frappe.whitelist(methods=["POST"])
def register_push_token(**kwargs):
    """Execute the v1 notifications.register_push_token endpoint."""
    return _register_push_token_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def deactivate_push_token(**kwargs):
    """Execute the v1 notifications.deactivate_push_token endpoint."""
    return _deactivate_push_token_impl(**_client_kwargs(kwargs))


@frappe.whitelist()
def list_notifications(**kwargs):
    """Execute the v1 notifications.list_notifications endpoint."""
    return _list_notifications_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def mark_notification_read(**kwargs):
    """Execute the v1 notifications.mark_notification_read endpoint."""
    return _mark_notification_read_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def mark_all_notifications_read(**kwargs):
    """Execute the v1 notifications.mark_all_notifications_read endpoint."""
    return _mark_all_notifications_read_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def delete_notification(**kwargs):
    """Execute the v1 notifications.delete_notification endpoint."""
    return _delete_notification_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def clear_notifications(**kwargs):
    """Execute the v1 notifications.clear_notifications endpoint."""
    return _clear_notifications_impl(**_client_kwargs(kwargs))
