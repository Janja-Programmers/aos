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
    get_presence_impl as _get_presence_impl,
    send_typing_event_impl as _send_typing_event_impl,
)

from aos.services.chat.api import run_chat_api
from aos.services.chat.endpoints import ENDPOINT_SPECS, TRANSACTIONAL_ENDPOINTS


def _call(name, implementation, kwargs):
    return run_chat_api(
        implementation,
        kwargs,
        spec=ENDPOINT_SPECS[name],
        operation_name=name,
        transactional=name in TRANSACTIONAL_ENDPOINTS,
    )

@frappe.whitelist(methods=["POST"])
def open_conversation(**kwargs):
    """Get existing conversation between two users or create a new one."""
    return _call("open_conversation", _open_conversation_impl, kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def list_conversations(**kwargs):
    """List current user's conversations."""
    return _call("list_conversations", _list_conversations_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def delete_conversation(**kwargs):
    """Soft delete/hide a conversation for the current user."""
    return _call("delete_conversation", _delete_conversation_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def send_message(**kwargs):
    """Send a message in a conversation."""
    return _call("send_message", _send_message_impl, kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def list_messages(**kwargs):
    """List messages for a conversation."""
    return _call("list_messages", _list_messages_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def forward_message(**kwargs):
    """Forward a visible message to one or more conversations."""
    return _call("forward_message", _forward_message_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def edit_message(**kwargs):
    """Edit a sent message."""
    return _call("edit_message", _edit_message_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def delete_messages(**kwargs):
    """Delete one or more messages for the current user or everyone."""
    return _call("delete_messages", _delete_messages_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def clear_chat(**kwargs):
    """Clear all visible messages in a conversation for the current user."""
    return _call("clear_chat", _clear_chat_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_message_star(**kwargs):
    """Star or unstar a message for the current user."""
    return _call("toggle_message_star", _toggle_message_star_impl, kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def list_starred_messages(**kwargs):
    """List current user's starred messages."""
    return _call("list_starred_messages", _list_starred_messages_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_message_reaction(**kwargs):
    """Add, change, or remove the current user's reaction to a message."""
    return _call("toggle_message_reaction", _toggle_message_reaction_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def translate_message(**kwargs):
    """Translate a visible text message for the current user."""
    return _call("translate_message", _translate_message_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def mark_delivered(**kwargs):
    """Mark incoming messages in a conversation as delivered."""
    return _call("mark_delivered", _mark_delivered_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def mark_read(**kwargs):
    """Mark incoming messages in a conversation as read."""
    return _call("mark_read", _mark_read_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def send_typing_event(**kwargs):
    """Send typing indicator for a conversation."""
    return _call("send_typing_event", _send_typing_event_impl, kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_presence(**kwargs):
    """Return the other participant's current online/last-seen snapshot."""
    return _call("get_presence", _get_presence_impl, kwargs)
