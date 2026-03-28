"""Shorts API endpoints (wrappers only).

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules (*_impl functions)
"""

import frappe

# UPLOAD
from .upload import (
    init_upload_impl,
    confirm_upload_impl,
    update_short_metadata_impl,
)

# FEED
from .feed import (
    feed_for_you_impl,
    feed_following_impl,
    feed_by_ad_impl,
)

# ENGAGEMENT
from .engagement import (
    toggle_like_impl,
)

# COMMENTS
from .comments import (
    add_comment_impl,
    reply_comment_impl,
    list_comments_impl,
    list_replies_impl,
    delete_comment_impl,
)

# TRACKING
from .tracking import (
    track_impression_impl,
    track_view_impl,
)

# MANAGEMENT
from .management import (
    get_short_impl,
    my_shorts_impl,
    delete_short_impl,
    retry_processing_impl,
)


# UPLOAD
@frappe.whitelist(methods=["POST"])
def init_upload(**kwargs):
    return init_upload_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def confirm_upload(**kwargs):
    return confirm_upload_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def update_short_metadata(**kwargs):
    return update_short_metadata_impl(**kwargs)


# FEED
@frappe.whitelist(allow_guest=True)
def feed_for_you(**kwargs):
    return feed_for_you_impl(**kwargs)


@frappe.whitelist()
def feed_following(**kwargs):
    return feed_following_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def feed_by_ad(**kwargs):
    return feed_by_ad_impl(**kwargs)


# ENGAGEMENT
@frappe.whitelist(methods=["POST"])
def toggle_like(**kwargs):
    return toggle_like_impl(**kwargs)


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


# TRACKING
@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_impression(**kwargs):
    return track_impression_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_view(**kwargs):
    return track_view_impl(**kwargs)


# MANAGEMENT
@frappe.whitelist(allow_guest=True)
def get_short(**kwargs):
    return get_short_impl(**kwargs)


@frappe.whitelist()
def my_shorts(**kwargs):
    return my_shorts_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_short(**kwargs):
    return delete_short_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def retry_processing(**kwargs):
    return retry_processing_impl(**kwargs)
