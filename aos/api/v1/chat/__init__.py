"""Public AOS API v1 wrappers for chat.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.chat.*.
Implementation stays in aos.api.chat implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.chat.conversation import (
    get_or_create_conversation_impl as _open_conversation_impl,
    list_conversations_impl as _list_conversations_impl,
    delete_conversation_impl as _delete_conversation_impl,
)
from aos.api.chat.message import (
    send_message_impl as _send_message_impl,
    list_messages_impl as _list_messages_impl,
)
from aos.api.chat.forward_message import (
    forward_message_impl as _forward_message_impl,
)
from aos.api.chat.edit_message import (
    edit_message_impl as _edit_message_impl,
)
from aos.api.chat.delete_messages import (
    delete_messages_impl as _delete_messages_impl,
)
from aos.api.chat.clear_chat import (
    clear_chat_impl as _clear_chat_impl,
)
from aos.api.chat.stars import (
    toggle_message_star_impl as _toggle_message_star_impl,
    list_starred_messages_impl as _list_starred_messages_impl,
)
from aos.api.chat.reactions import (
    toggle_message_reaction_impl as _toggle_message_reaction_impl,
)
from aos.api.chat.translate_message import (
    translate_message_impl as _translate_message_impl,
)
from aos.api.chat.status import (
    mark_delivered_impl as _mark_delivered_impl,
    mark_read_impl as _mark_read_impl,
)
from aos.api.chat.presence import (
    send_typing_event_impl as _send_typing_event_impl,
)

@frappe.whitelist(methods=["POST"])
def open_conversation(**kwargs):
    """Get existing conversation between two users or create a new one."""
    return _open_conversation_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def list_conversations(**kwargs):
    """List current user's conversations."""
    return _list_conversations_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_conversation(**kwargs):
    """Soft delete/hide a conversation for the current user."""
    return _delete_conversation_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def send_message(**kwargs):
    """Send a message in a conversation."""
    return _send_message_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def list_messages(**kwargs):
    """List messages for a conversation."""
    return _list_messages_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def forward_message(**kwargs):
    """Forward a visible message to one or more conversations."""
    return _forward_message_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def edit_message(**kwargs):
    """Edit a sent message."""
    return _edit_message_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_messages(**kwargs):
    """Delete one or more messages for the current user or everyone."""
    return _delete_messages_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def clear_chat(**kwargs):
    """Clear all visible messages in a conversation for the current user."""
    return _clear_chat_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_message_star(**kwargs):
    """Star or unstar a message for the current user."""
    return _toggle_message_star_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def list_starred_messages(**kwargs):
    """List current user's starred messages."""
    return _list_starred_messages_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_message_reaction(**kwargs):
    """Add, change, or remove the current user's reaction to a message."""
    return _toggle_message_reaction_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def translate_message(**kwargs):
    """Translate a visible text message for the current user."""
    return _translate_message_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def mark_delivered(**kwargs):
    """Mark incoming messages in a conversation as delivered."""
    return _mark_delivered_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def mark_read(**kwargs):
    """Mark incoming messages in a conversation as read."""
    return _mark_read_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def send_typing_event(**kwargs):
    """Send typing indicator for a conversation."""
    return _send_typing_event_impl(**kwargs)
