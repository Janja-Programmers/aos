"""Stable public AOS Live v1 endpoints.

The wrappers strip only Frappe-owned transport metadata, validate strict field
contracts and delegate to the established implementation modules.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import frappe

from aos.api.shared.transport import client_kwargs as _client_kwargs
from aos.services.live.api import run_live_api
from aos.services.live.endpoints import ENDPOINT_SPECS, TRANSACTIONAL_ENDPOINTS

from aos.api.live.share import share_live_to_chat_impl as _share_live_to_chat_impl
from aos.api.live.live import (
    end_live_impl as _end_live_impl,
    get_live_impl as _get_live_impl,
    join_live_impl as _join_live_impl,
    list_live_streams_impl as _list_live_streams_impl,
    start_live_impl as _start_live_impl,
)
from aos.api.live.token import (
    get_live_cohost_token_impl as _get_live_cohost_token_impl,
    get_live_token_impl as _get_live_token_impl,
)
from aos.api.live.tracking import track_join_impl as _track_join_impl, track_leave_impl as _track_leave_impl
from aos.api.live.messages import (
    add_live_message_impl as _add_live_message_impl,
    delete_live_message_impl as _delete_live_message_impl,
    list_live_messages_impl as _list_live_messages_impl,
    list_live_replies_impl as _list_live_replies_impl,
    reply_live_message_impl as _reply_live_message_impl,
)
from aos.api.live.reactions import send_reaction_impl as _send_reaction_impl
from aos.api.live.cohost import (
    activate_live_cohost_impl as _activate_live_cohost_impl,
    cancel_live_cohost_impl as _cancel_live_cohost_impl,
    end_live_cohost_impl as _end_live_cohost_impl,
    get_live_cohost_impl as _get_live_cohost_impl,
    invite_live_cohost_impl as _invite_live_cohost_impl,
    list_live_cohosts_impl as _list_live_cohosts_impl,
    request_live_cohost_impl as _request_live_cohost_impl,
    respond_live_cohost_impl as _respond_live_cohost_impl,
)


def _call(name: str, implementation: Callable[..., dict[str, Any]], kwargs: dict[str, Any]) -> dict[str, Any]:
    return run_live_api(
        implementation,
        _client_kwargs(kwargs),
        spec=ENDPOINT_SPECS[name],
        operation_name=name,
        transactional=name in TRANSACTIONAL_ENDPOINTS,
    )


@frappe.whitelist(methods=["POST"])
def start_live(**kwargs):
    return _call("start_live", _start_live_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def share_live_to_chat(**kwargs):
    return _call("share_live_to_chat", _share_live_to_chat_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def join_live(**kwargs):
    return _call("join_live", _join_live_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def end_live(**kwargs):
    return _call("end_live", _end_live_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_live(**kwargs):
    return _call("get_live", _get_live_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_live_streams(**kwargs):
    return _call("list_live_streams", _list_live_streams_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def get_live_token(**kwargs):
    return _call("get_live_token", _get_live_token_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def get_live_cohost_token(**kwargs):
    return _call("get_live_cohost_token", _get_live_cohost_token_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_join(**kwargs):
    return _call("track_join", _track_join_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_leave(**kwargs):
    return _call("track_leave", _track_leave_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def add_live_message(**kwargs):
    return _call("add_live_message", _add_live_message_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def reply_live_message(**kwargs):
    return _call("reply_live_message", _reply_live_message_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_live_messages(**kwargs):
    return _call("list_live_messages", _list_live_messages_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_live_replies(**kwargs):
    return _call("list_live_replies", _list_live_replies_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def delete_live_message(**kwargs):
    return _call("delete_live_message", _delete_live_message_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def send_reaction(**kwargs):
    return _call("send_reaction", _send_reaction_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def invite_live_cohost(**kwargs):
    return _call("invite_live_cohost", _invite_live_cohost_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def request_live_cohost(**kwargs):
    return _call("request_live_cohost", _request_live_cohost_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def respond_live_cohost(**kwargs):
    return _call("respond_live_cohost", _respond_live_cohost_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def cancel_live_cohost(**kwargs):
    return _call("cancel_live_cohost", _cancel_live_cohost_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def activate_live_cohost(**kwargs):
    return _call("activate_live_cohost", _activate_live_cohost_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def end_live_cohost(**kwargs):
    return _call("end_live_cohost", _end_live_cohost_impl, kwargs)


@frappe.whitelist(methods=["GET"])
def get_live_cohost(**kwargs):
    return _call("get_live_cohost", _get_live_cohost_impl, kwargs)


@frappe.whitelist(methods=["GET"])
def list_live_cohosts(**kwargs):
    return _call("list_live_cohosts", _list_live_cohosts_impl, kwargs)
