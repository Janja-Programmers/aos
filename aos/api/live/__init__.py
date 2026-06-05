"""
Live Stream endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

# LIFECYCLE
from .live import (
    start_live_impl,
    join_live_impl,
    end_live_impl,
    get_live_impl,
    list_live_streams_impl,
)

# TOKEN
from .token import (
    get_live_token_impl,
)

# TRACKING
from .tracking import (
    track_join_impl,
    track_leave_impl,
)

# MESSAGES
from .messages import (
    add_live_message_impl,
    reply_live_message_impl,
    list_live_messages_impl,
    list_live_replies_impl,
    delete_live_message_impl,
)

# REACTIONS
from .reactions import (
    send_reaction_impl,
)


# LIFECYCLE
@frappe.whitelist(methods=["POST"])
def start_live(**kwargs):
    return start_live_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def join_live(**kwargs):
    return join_live_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def end_live(**kwargs):
    return end_live_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_live(**kwargs):
    return get_live_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def list_live_streams(**kwargs):
    return list_live_streams_impl(**kwargs)


# TOKEN
@frappe.whitelist(methods=["POST"])
def get_live_token(**kwargs):
    return get_live_token_impl(**kwargs)


# TRACKING
@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_join(**kwargs):
    return track_join_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_leave(**kwargs):
    return track_leave_impl(**kwargs)


# LIVE MESSAGES
@frappe.whitelist(methods=["POST"])
def add_live_message(**kwargs):
    return add_live_message_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def reply_live_message(**kwargs):
    return reply_live_message_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def list_live_messages(**kwargs):
    return list_live_messages_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def list_live_replies(**kwargs):
    return list_live_replies_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_live_message(**kwargs):
    return delete_live_message_impl(**kwargs)


# REACTIONS
@frappe.whitelist(methods=["POST"])
def send_reaction(**kwargs):
    return send_reaction_impl(**kwargs)
