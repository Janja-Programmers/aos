"""Strict field contracts for the public Chat v1 API."""

from __future__ import annotations

from .validation import (
    AD_ID_RE,
    CONVERSATION_ID_RE,
    LIVE_ID_RE,
    MEDIA_ID_RE,
    MESSAGE_ID_RE,
    PUBLIC_ACCOUNT_ID_RE,
    SHORT_ID_RE,
    EndpointSpec,
)


def _spec(fields: set[str], *, ids=()) -> EndpointSpec:
    return EndpointSpec(frozenset(fields), tuple(ids))


_LOCK = {"lock_token"}

ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "open_conversation": _spec({"user"}),
    "create_group": _spec({"title", "participant_ids", "avatar_media_id"}, ids=(("avatar_media_id", MEDIA_ID_RE),)),
    "update_group": _spec({"conversation_id", "title", "avatar_media_id", "remove_avatar", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE), ("avatar_media_id", MEDIA_ID_RE))),
    "add_group_members": _spec({"conversation_id", "participant_ids", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "remove_group_member": _spec({"conversation_id", "account_id", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE), ("account_id", PUBLIC_ACCOUNT_ID_RE))),
    "set_group_member_role": _spec({"conversation_id", "account_id", "role", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE), ("account_id", PUBLIC_ACCOUNT_ID_RE))),
    "transfer_group_ownership": _spec({"conversation_id", "account_id", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE), ("account_id", PUBLIC_ACCOUNT_ID_RE))),
    "leave_group": _spec({"conversation_id", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "list_group_members": _spec({"conversation_id", "limit", "cursor", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "list_conversations": _spec({"limit", "cursor"}),
    "list_locked_conversations": _spec({"limit", "cursor", "lock_token"}),
    "delete_conversation": _spec({"conversation_id", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "set_conversation_lock": _spec({"conversation_id", "locked", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "configure_chat_lock_secret": _spec({"secret", "current_secret", "hide_locked_chats"}),
    "verify_chat_lock_secret": _spec({"secret"}),
    "remove_chat_lock_secret": _spec({"current_secret"}),
    "get_chat_lock_state": _spec({"lock_token"}),
    "send_message": _spec(
        {"conversation_id", "content", "attachments", "ad", "short", "live", "reply_to_message", "idempotency_key", "lock_token"},
        ids=(("conversation_id", CONVERSATION_ID_RE), ("reply_to_message", MESSAGE_ID_RE), ("ad", AD_ID_RE), ("short", SHORT_ID_RE), ("live", LIVE_ID_RE)),
    ),
    "list_messages": _spec({"conversation_id", "limit", "cursor", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "forward_message": _spec({"message_id", "target_conversation_ids", "idempotency_key", "lock_token"}, ids=(("message_id", MESSAGE_ID_RE),)),
    "edit_message": _spec({"message_id", "content", "lock_token"}, ids=(("message_id", MESSAGE_ID_RE),)),
    "delete_messages": _spec({"message_ids", "delete_scope", "lock_token"}),
    "clear_chat": _spec({"conversation_id", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "set_message_star": _spec({"message_id", "starred", "lock_token"}, ids=(("message_id", MESSAGE_ID_RE),)),
    "list_starred_messages": _spec({"limit", "conversation_id", "cursor", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "set_message_reaction": _spec({"message_id", "emoji", "lock_token"}, ids=(("message_id", MESSAGE_ID_RE),)),
    "translate_message": _spec({"message_id", "target_language", "source_language", "force_refresh", "lock_token"}, ids=(("message_id", MESSAGE_ID_RE),)),
    "mark_delivered": _spec({"conversation_id", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "mark_read": _spec({"conversation_id", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "send_typing_event": _spec({"conversation_id", "is_typing", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "get_presence": _spec({"conversation_id", "lock_token"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
}

TRANSACTIONAL_ENDPOINTS = frozenset({
    "open_conversation", "create_group", "update_group", "add_group_members", "remove_group_member",
    "set_group_member_role", "transfer_group_ownership", "leave_group", "delete_conversation",
    "set_conversation_lock", "configure_chat_lock_secret", "verify_chat_lock_secret", "remove_chat_lock_secret",
    "send_message", "forward_message", "edit_message", "delete_messages", "clear_chat", "set_message_star",
    "set_message_reaction", "translate_message", "mark_delivered", "mark_read", "send_typing_event",
})
