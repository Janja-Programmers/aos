"""Chat endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

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


@frappe.whitelist()
def list_conversations(**kwargs):
    """List current user's conversations (chat list)."""
    return list_conversations_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_conversation(**kwargs):
    """Soft delete (hide) a conversation for current user."""
    return delete_conversation_impl(**kwargs)


# Message APIs
@frappe.whitelist(methods=["POST"])
def send_message(**kwargs):
    """Send a message in a conversation."""
    return send_message_impl(**kwargs)


@frappe.whitelist()
def list_messages(**kwargs):
    """List messages for a conversation (paginated)."""
    return list_messages_impl(**kwargs)


# Status APIs
@frappe.whitelist(methods=["POST"])
def mark_delivered(**kwargs):
    """Mark messages as delivered."""
    return mark_delivered_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def mark_read(**kwargs):
    """Mark messages as read and reset unread count."""
    return mark_read_impl(**kwargs)


# Realtime APIs
@frappe.whitelist(methods=["POST"])
def typing(**kwargs):
    """Send typing indicator for a conversation (realtime)."""
    return send_typing_event_impl(**kwargs)
