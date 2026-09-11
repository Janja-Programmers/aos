"""Canonical public AOS API v1 wrappers for Notifications."""

from __future__ import annotations

import frappe

from aos.api.notifications.callback import (
    handle_delivery_callback_impl as _handle_delivery_callback_impl,
)
from aos.api.notifications.notification import (
    clear_notifications_impl as _clear_notifications_impl,
    delete_notification_impl as _delete_notification_impl,
    list_notifications_impl as _list_notifications_impl,
    mark_all_notifications_read_impl as _mark_all_notifications_read_impl,
    mark_notification_read_impl as _mark_notification_read_impl,
)
from aos.api.notifications.push_config import get_push_config_impl as _get_push_config_impl
from aos.api.notifications.token import (
    deactivate_push_token_impl as _deactivate_push_token_impl,
    register_push_token_impl as _register_push_token_impl,
)
from aos.api.shared.transport import execute_endpoint as _execute_endpoint


@frappe.whitelist(methods=["GET"])
def get_push_config(**kwargs):
    """Return authenticated public Firebase Web Messaging bootstrap configuration."""
    return _execute_endpoint(_get_push_config_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def register_push_token(**kwargs):
    """Register or rotate a push registration owned by the authenticated account."""
    return _execute_endpoint(_register_push_token_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def deactivate_push_token(**kwargs):
    """Deactivate a push registration owned by the authenticated account."""
    return _execute_endpoint(_deactivate_push_token_impl, kwargs)


@frappe.whitelist(methods=["GET"])
def list_notifications(**kwargs):
    """List the authenticated account's Notification inbox."""
    return _execute_endpoint(_list_notifications_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def mark_notification_read(**kwargs):
    """Idempotently mark one owned notification read."""
    return _execute_endpoint(_mark_notification_read_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def mark_all_notifications_read(**kwargs):
    """Mark the authenticated account's current unread notifications read."""
    return _execute_endpoint(_mark_all_notifications_read_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def delete_notification(**kwargs):
    """Delete one owned notification."""
    return _execute_endpoint(_delete_notification_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def clear_notifications(**kwargs):
    """Clear owned notifications in the selected canonical category."""
    return _execute_endpoint(_clear_notifications_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_delivery_callback(**kwargs):
    """Handle the signed callback from the private Notifications delivery companion."""
    return _execute_endpoint(_handle_delivery_callback_impl, kwargs)
