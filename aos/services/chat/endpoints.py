"""Strict field contracts for the public Chat v1 API."""

from __future__ import annotations

from .validation import AD_ID_RE, CONVERSATION_ID_RE, LIVE_ID_RE, MESSAGE_ID_RE, SHORT_ID_RE, EndpointSpec


def _spec(fields: set[str], *, ids=()) -> EndpointSpec:
    return EndpointSpec(frozenset(fields), tuple(ids))


ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "open_conversation": _spec({"user"}),
    "list_conversations": _spec({"limit", "cursor"}),
    "delete_conversation": _spec({"conversation_id"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "send_message": _spec(
        {"conversation_id", "content", "attachments", "ad", "short", "live", "reply_to_message", "idempotency_key"},
        ids=(
            ("conversation_id", CONVERSATION_ID_RE),
            ("reply_to_message", MESSAGE_ID_RE),
            ("ad", AD_ID_RE),
            ("short", SHORT_ID_RE),
            ("live", LIVE_ID_RE),
        ),
    ),
    "list_messages": _spec(
        {"conversation_id", "limit", "cursor"},
        ids=(("conversation_id", CONVERSATION_ID_RE),),
    ),
    "forward_message": _spec(
        {"message_id", "target_conversation_ids", "idempotency_key"},
        ids=(("message_id", MESSAGE_ID_RE),),
    ),
    "edit_message": _spec({"message_id", "content"}, ids=(("message_id", MESSAGE_ID_RE),)),
    "delete_messages": _spec(
        {"message_ids", "delete_scope"},
    ),
    "clear_chat": _spec({"conversation_id"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "set_message_star": _spec({"message_id", "starred"}, ids=(("message_id", MESSAGE_ID_RE),)),
    "list_starred_messages": _spec(
        {"limit", "conversation_id", "cursor"},
        ids=(("conversation_id", CONVERSATION_ID_RE),),
    ),
    "set_message_reaction": _spec({"message_id", "emoji"}, ids=(("message_id", MESSAGE_ID_RE),)),
    "translate_message": _spec({"message_id", "target_language", "source_language", "force_refresh"}, ids=(("message_id", MESSAGE_ID_RE),)),
    "mark_delivered": _spec({"conversation_id"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "mark_read": _spec({"conversation_id"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "send_typing_event": _spec({"conversation_id", "is_typing"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "get_presence": _spec({"conversation_id"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
}

TRANSACTIONAL_ENDPOINTS = frozenset({
    "open_conversation", "delete_conversation", "send_message", "forward_message", "edit_message",
    "delete_messages", "clear_chat", "set_message_star", "set_message_reaction",
    "translate_message", "mark_delivered", "mark_read", "send_typing_event",
})
