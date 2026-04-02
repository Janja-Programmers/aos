"""
Notification endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

# Notification APIs
from .notification import (
    list_notifications_impl,
    mark_notification_read_impl,
    mark_all_notifications_read_impl,
)

# Push Token APIs
from .token import (
    register_push_token_impl,
    deactivate_push_token_impl,
)


# PUSH TOKEN
@frappe.whitelist(methods=["POST"])
def register_push_token(**kwargs):
    return register_push_token_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def deactivate_push_token(**kwargs):
    return deactivate_push_token_impl(**kwargs)


# NOTIFICATIONS
@frappe.whitelist()
def list_notifications(**kwargs):
    return list_notifications_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def mark_notification_read(**kwargs):
    return mark_notification_read_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def mark_all_notifications_read(**kwargs):
    return mark_all_notifications_read_impl(**kwargs)
