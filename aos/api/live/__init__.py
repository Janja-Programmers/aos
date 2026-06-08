"""
Live Stream endpoints.

Structure:
- Whitelisted endpoint wrappers are defined here.
- Business logic is implemented in sibling modules.
- Public read/watch endpoints may allow guests.
- Interaction, moderation, token, and co-host endpoints require login.
"""

import frappe


# LIFECYCLE
from .live import (
    end_live_impl,
    get_live_impl,
    join_live_impl,
    list_live_streams_impl,
    start_live_impl,
)

# TOKEN
from .token import (
    get_live_cohost_token_impl,
    get_live_token_impl,
)

# TRACKING
from .tracking import (
    track_join_impl,
    track_leave_impl,
)

# LIVE MESSAGES
from .messages import (
    add_live_message_impl,
    delete_live_message_impl,
    list_live_messages_impl,
    list_live_replies_impl,
    reply_live_message_impl,
)

# REACTIONS
from .reactions import (
    send_reaction_impl,
)

# CO-HOST
from .cohost import (
    activate_live_cohost_impl,
    cancel_live_cohost_impl,
    end_live_cohost_impl,
    get_live_cohost_impl,
    invite_live_cohost_impl,
    list_live_cohosts_impl,
    request_live_cohost_impl,
    respond_live_cohost_impl,
)


# LIFECYCLE
@frappe.whitelist(methods=["POST"])
def start_live(**kwargs):
    return start_live_impl(
        **kwargs
    )


@frappe.whitelist(
    allow_guest=True,
    methods=["POST"],
)
def join_live(**kwargs):
    return join_live_impl(
        **kwargs
    )


@frappe.whitelist(methods=["POST"])
def end_live(**kwargs):
    return end_live_impl(
        **kwargs
    )


@frappe.whitelist(
    allow_guest=True,
    methods=["GET"],
)
def get_live(**kwargs):
    return get_live_impl(
        **kwargs
    )


@frappe.whitelist(
    allow_guest=True,
    methods=["GET"],
)
def list_live_streams(**kwargs):
    return list_live_streams_impl(
        **kwargs
    )


# TOKEN
@frappe.whitelist(methods=["POST"])
def get_live_token(**kwargs):
    return get_live_token_impl(
        **kwargs
    )


@frappe.whitelist(methods=["POST"])
def get_live_cohost_token(**kwargs):
    return get_live_cohost_token_impl(
        **kwargs
    )


# TRACKING
@frappe.whitelist(
    allow_guest=True,
    methods=["POST"],
)
def track_join(**kwargs):
    return track_join_impl(
        **kwargs
    )


@frappe.whitelist(
    allow_guest=True,
    methods=["POST"],
)
def track_leave(**kwargs):
    return track_leave_impl(
        **kwargs
    )


# LIVE MESSAGES
@frappe.whitelist(methods=["POST"])
def add_live_message(**kwargs):
    return add_live_message_impl(
        **kwargs
    )


@frappe.whitelist(methods=["POST"])
def reply_live_message(**kwargs):
    return reply_live_message_impl(
        **kwargs
    )


@frappe.whitelist(
    allow_guest=True,
    methods=["GET"],
)
def list_live_messages(**kwargs):
    return list_live_messages_impl(
        **kwargs
    )


@frappe.whitelist(
    allow_guest=True,
    methods=["GET"],
)
def list_live_replies(**kwargs):
    return list_live_replies_impl(
        **kwargs
    )


@frappe.whitelist(methods=["POST"])
def delete_live_message(**kwargs):
    return delete_live_message_impl(
        **kwargs
    )


# REACTIONS
@frappe.whitelist(methods=["POST"])
def send_reaction(**kwargs):
    return send_reaction_impl(
        **kwargs
    )


# CO-HOST WORKFLOW
@frappe.whitelist(methods=["POST"])
def invite_live_cohost(**kwargs):
    return invite_live_cohost_impl(
        **kwargs
    )


@frappe.whitelist(methods=["POST"])
def request_live_cohost(**kwargs):
    return request_live_cohost_impl(
        **kwargs
    )


@frappe.whitelist(methods=["POST"])
def respond_live_cohost(**kwargs):
    return respond_live_cohost_impl(
        **kwargs
    )


@frappe.whitelist(methods=["POST"])
def cancel_live_cohost(**kwargs):
    return cancel_live_cohost_impl(
        **kwargs
    )


@frappe.whitelist(methods=["POST"])
def activate_live_cohost(**kwargs):
    return activate_live_cohost_impl(
        **kwargs
    )


@frappe.whitelist(methods=["POST"])
def end_live_cohost(**kwargs):
    return end_live_cohost_impl(
        **kwargs
    )


@frappe.whitelist(methods=["GET"])
def get_live_cohost(**kwargs):
    return get_live_cohost_impl(
        **kwargs
    )


@frappe.whitelist(methods=["GET"])
def list_live_cohosts(**kwargs):
    return list_live_cohosts_impl(
        **kwargs
    )
