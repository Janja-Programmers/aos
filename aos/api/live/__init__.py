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

# COMMENTS
from .comments import (
    add_comment_impl,
    reply_comment_impl,
    list_comments_impl,
    list_replies_impl,
    delete_comment_impl,
)

# REACTIONS
from .reactions import (
    send_reaction_impl,
)

# ADS
from .ads import (
    attach_ad_impl,
    remove_ad_impl,
    pin_ad_impl,
    list_live_ads_impl,
)


# LIFECYCLE
@frappe.whitelist(methods=["POST"])
def start_live(**kwargs):
    return start_live_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
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


# COMMENTS
@frappe.whitelist(methods=["POST"])
def add_comment(**kwargs):
    return add_comment_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def reply_comment(**kwargs):
    return reply_comment_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def list_comments(**kwargs):
    return list_comments_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def list_replies(**kwargs):
    return list_replies_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_comment(**kwargs):
    return delete_comment_impl(**kwargs)


# REACTIONS
@frappe.whitelist(allow_guest=True, methods=["POST"])
def send_reaction(**kwargs):
    return send_reaction_impl(**kwargs)


# ADS
@frappe.whitelist(methods=["POST"])
def attach_ad(**kwargs):
    return attach_ad_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def remove_ad(**kwargs):
    return remove_ad_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def pin_ad(**kwargs):
    return pin_ad_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def list_live_ads(**kwargs):
    return list_live_ads_impl(**kwargs)
