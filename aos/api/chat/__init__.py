"""Chat endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

from __future__ import annotations

import frappe

# Conversation
from .conversation import (
    get_or_create_conversation_impl,
    list_conversations_impl,
    delete_conversation_impl,
)

# Message
from .message import (
    send_message_impl,
    list_messages_impl,
)

# Status
from .status import (
    mark_delivered_impl,
    mark_read_impl,
)

# Realtime / Presence
from .presence import (
    send_typing_event_impl,
)


# Conversation APIs
@frappe.whitelist(methods=["POST"])
def open_conversation(**kwargs):
    """Get existing conversation between two users or create a new one."""
    return get_or_create_conversation_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def list_conversations(**kwargs):
    """List current user's conversations."""
    return list_conversations_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_conversation(**kwargs):
    """Soft delete/hide a conversation for the current user."""
    return delete_conversation_impl(**kwargs)


# Message APIs
@frappe.whitelist(methods=["POST"])
def send_message(**kwargs):
    """Send a message in a conversation."""
    return send_message_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def list_messages(**kwargs):
    """List messages for a conversation."""
    return list_messages_impl(**kwargs)


# Status APIs
@frappe.whitelist(methods=["POST"])
def mark_delivered(**kwargs):
    """Mark incoming messages in a conversation as delivered."""
    return mark_delivered_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def mark_read(**kwargs):
    """Mark incoming messages in a conversation as read."""
    return mark_read_impl(**kwargs)


# Realtime / Presence APIs
@frappe.whitelist(methods=["POST"])
def send_typing_event(**kwargs):
    """Send typing indicator for a conversation."""
    return send_typing_event_impl(**kwargs)
