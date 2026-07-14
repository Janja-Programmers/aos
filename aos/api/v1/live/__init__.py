"""Public AOS API v1 wrappers for live.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.live.*.
Implementation stays in aos.api.live implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.live.live import (
    start_live_impl as _start_live_impl,
    join_live_impl as _join_live_impl,
    end_live_impl as _end_live_impl,
    get_live_impl as _get_live_impl,
    list_live_streams_impl as _list_live_streams_impl,
)
from aos.api.live.token import (
    get_live_token_impl as _get_live_token_impl,
    get_live_cohost_token_impl as _get_live_cohost_token_impl,
)
from aos.api.live.tracking import (
    track_join_impl as _track_join_impl,
    track_leave_impl as _track_leave_impl,
)
from aos.api.live.messages import (
    add_live_message_impl as _add_live_message_impl,
    reply_live_message_impl as _reply_live_message_impl,
    list_live_messages_impl as _list_live_messages_impl,
    list_live_replies_impl as _list_live_replies_impl,
    delete_live_message_impl as _delete_live_message_impl,
)
from aos.api.live.reactions import (
    send_reaction_impl as _send_reaction_impl,
)
from aos.api.live.cohost import (
    invite_live_cohost_impl as _invite_live_cohost_impl,
    request_live_cohost_impl as _request_live_cohost_impl,
    respond_live_cohost_impl as _respond_live_cohost_impl,
    cancel_live_cohost_impl as _cancel_live_cohost_impl,
    activate_live_cohost_impl as _activate_live_cohost_impl,
    end_live_cohost_impl as _end_live_cohost_impl,
    get_live_cohost_impl as _get_live_cohost_impl,
    list_live_cohosts_impl as _list_live_cohosts_impl,
)

@frappe.whitelist(methods=["POST"])
def start_live(**kwargs):
    """Execute the v1 live.start_live endpoint."""
    return _start_live_impl(**kwargs)


@frappe.whitelist(
    allow_guest=True,
    methods=["POST"],
)
def join_live(**kwargs):
    """Execute the v1 live.join_live endpoint."""
    return _join_live_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def end_live(**kwargs):
    """Execute the v1 live.end_live endpoint."""
    return _end_live_impl(**kwargs)


@frappe.whitelist(
    allow_guest=True,
    methods=["GET"],
)
def get_live(**kwargs):
    """Execute the v1 live.get_live endpoint."""
    return _get_live_impl(**kwargs)


@frappe.whitelist(
    allow_guest=True,
    methods=["GET"],
)
def list_live_streams(**kwargs):
    """Execute the v1 live.list_live_streams endpoint."""
    return _list_live_streams_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def get_live_token(**kwargs):
    """Execute the v1 live.get_live_token endpoint."""
    return _get_live_token_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def get_live_cohost_token(**kwargs):
    """Execute the v1 live.get_live_cohost_token endpoint."""
    return _get_live_cohost_token_impl(**kwargs)


@frappe.whitelist(
    allow_guest=True,
    methods=["POST"],
)
def track_join(**kwargs):
    """Execute the v1 live.track_join endpoint."""
    return _track_join_impl(**kwargs)


@frappe.whitelist(
    allow_guest=True,
    methods=["POST"],
)
def track_leave(**kwargs):
    """Execute the v1 live.track_leave endpoint."""
    return _track_leave_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def add_live_message(**kwargs):
    """Execute the v1 live.add_live_message endpoint."""
    return _add_live_message_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def reply_live_message(**kwargs):
    """Execute the v1 live.reply_live_message endpoint."""
    return _reply_live_message_impl(**kwargs)


@frappe.whitelist(
    allow_guest=True,
    methods=["GET"],
)
def list_live_messages(**kwargs):
    """Execute the v1 live.list_live_messages endpoint."""
    return _list_live_messages_impl(**kwargs)


@frappe.whitelist(
    allow_guest=True,
    methods=["GET"],
)
def list_live_replies(**kwargs):
    """Execute the v1 live.list_live_replies endpoint."""
    return _list_live_replies_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_live_message(**kwargs):
    """Execute the v1 live.delete_live_message endpoint."""
    return _delete_live_message_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def send_reaction(**kwargs):
    """Execute the v1 live.send_reaction endpoint."""
    return _send_reaction_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def invite_live_cohost(**kwargs):
    """Execute the v1 live.invite_live_cohost endpoint."""
    return _invite_live_cohost_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def request_live_cohost(**kwargs):
    """Execute the v1 live.request_live_cohost endpoint."""
    return _request_live_cohost_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def respond_live_cohost(**kwargs):
    """Execute the v1 live.respond_live_cohost endpoint."""
    return _respond_live_cohost_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def cancel_live_cohost(**kwargs):
    """Execute the v1 live.cancel_live_cohost endpoint."""
    return _cancel_live_cohost_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def activate_live_cohost(**kwargs):
    """Execute the v1 live.activate_live_cohost endpoint."""
    return _activate_live_cohost_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def end_live_cohost(**kwargs):
    """Execute the v1 live.end_live_cohost endpoint."""
    return _end_live_cohost_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def get_live_cohost(**kwargs):
    """Execute the v1 live.get_live_cohost endpoint."""
    return _get_live_cohost_impl(**kwargs)


@frappe.whitelist(methods=["GET"])
def list_live_cohosts(**kwargs):
    """Execute the v1 live.list_live_cohosts endpoint."""
    return _list_live_cohosts_impl(**kwargs)
