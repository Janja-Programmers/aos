"""Canonical public AOS Chat v1 API.

Only this module is whitelisted. Domain implementation lives in aos.services.chat.
"""
from __future__ import annotations

import frappe

from aos.services.chat.api import run_chat_api
from aos.services.chat.conversation_ops import (
    add_group_members_impl, create_group_impl, delete_conversation_impl, leave_group_impl,
    list_conversations_impl, list_group_members_impl, list_locked_conversations_impl,
    open_conversation_impl, remove_group_member_impl, set_group_member_role_impl,
    transfer_group_ownership_impl, update_group_impl,
)
from aos.services.chat.endpoints import ENDPOINT_SPECS, TRANSACTIONAL_ENDPOINTS
from aos.services.chat.lock_ops import (
    configure_chat_lock_secret_impl, get_chat_lock_state_impl, remove_chat_lock_secret_impl,
    set_conversation_lock_impl, verify_chat_lock_secret_impl,
)
from aos.services.chat.message_mutations import (
    clear_chat_impl, delete_messages_impl, edit_message_impl, forward_message_impl,
    list_starred_messages_impl, set_message_reaction_impl, set_message_star_impl,
)
from aos.services.chat.message_ops import list_messages_impl, send_message_impl
from aos.services.chat.presence_ops import get_presence_impl, send_typing_event_impl
from aos.services.chat.status_ops import mark_delivered_impl, mark_read_impl
from aos.services.chat.translation_ops import translate_message_impl


def _call(name, implementation, kwargs):
    return run_chat_api(implementation, kwargs, spec=ENDPOINT_SPECS[name], operation_name=name,
                        transactional=name in TRANSACTIONAL_ENDPOINTS)


def _post(name, impl, doc):
    return frappe.whitelist(methods=["POST"])(doc)


@frappe.whitelist(methods=["POST"])
def open_conversation(**kwargs): return _call("open_conversation", open_conversation_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def create_group(**kwargs): return _call("create_group", create_group_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def update_group(**kwargs): return _call("update_group", update_group_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def add_group_members(**kwargs): return _call("add_group_members", add_group_members_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def remove_group_member(**kwargs): return _call("remove_group_member", remove_group_member_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def set_group_member_role(**kwargs): return _call("set_group_member_role", set_group_member_role_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def transfer_group_ownership(**kwargs): return _call("transfer_group_ownership", transfer_group_ownership_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def leave_group(**kwargs): return _call("leave_group", leave_group_impl, kwargs)

@frappe.whitelist(methods=["GET"])
def list_group_members(**kwargs): return _call("list_group_members", list_group_members_impl, kwargs)

@frappe.whitelist(methods=["GET"])
def list_conversations(**kwargs): return _call("list_conversations", list_conversations_impl, kwargs)

@frappe.whitelist(methods=["GET"])
def list_locked_conversations(**kwargs): return _call("list_locked_conversations", list_locked_conversations_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def delete_conversation(**kwargs): return _call("delete_conversation", delete_conversation_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def set_conversation_lock(**kwargs): return _call("set_conversation_lock", set_conversation_lock_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def configure_chat_lock_secret(**kwargs): return _call("configure_chat_lock_secret", configure_chat_lock_secret_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def verify_chat_lock_secret(**kwargs): return _call("verify_chat_lock_secret", verify_chat_lock_secret_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def remove_chat_lock_secret(**kwargs): return _call("remove_chat_lock_secret", remove_chat_lock_secret_impl, kwargs)

@frappe.whitelist(methods=["GET"])
def get_chat_lock_state(**kwargs): return _call("get_chat_lock_state", get_chat_lock_state_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def send_message(**kwargs): return _call("send_message", send_message_impl, kwargs)

@frappe.whitelist(methods=["GET"])
def list_messages(**kwargs): return _call("list_messages", list_messages_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def forward_message(**kwargs): return _call("forward_message", forward_message_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def edit_message(**kwargs): return _call("edit_message", edit_message_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def delete_messages(**kwargs): return _call("delete_messages", delete_messages_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def clear_chat(**kwargs): return _call("clear_chat", clear_chat_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def set_message_star(**kwargs): return _call("set_message_star", set_message_star_impl, kwargs)

@frappe.whitelist(methods=["GET"])
def list_starred_messages(**kwargs): return _call("list_starred_messages", list_starred_messages_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def set_message_reaction(**kwargs): return _call("set_message_reaction", set_message_reaction_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def translate_message(**kwargs): return _call("translate_message", translate_message_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def mark_delivered(**kwargs): return _call("mark_delivered", mark_delivered_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def mark_read(**kwargs): return _call("mark_read", mark_read_impl, kwargs)

@frappe.whitelist(methods=["POST"])
def send_typing_event(**kwargs): return _call("send_typing_event", send_typing_event_impl, kwargs)

@frappe.whitelist(methods=["GET"])
def get_presence(**kwargs): return _call("get_presence", get_presence_impl, kwargs)
