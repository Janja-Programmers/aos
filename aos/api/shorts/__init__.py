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

# LIBRARY / ACTIONS
from .library import (
    toggle_save_short_impl,
    saved_shorts_impl,
    liked_shorts_impl,
    reposted_shorts_impl,
    toggle_repost_impl,
    download_short_impl,
)

# SHARING
from .share import (
    create_short_share_link_impl,
    share_short_to_chat_impl,
)

# COMMENTS
from .comments import (
    add_comment_impl,
    reply_comment_impl,
    list_comments_impl,
    list_replies_impl,
    delete_comment_impl,
    toggle_comment_like_impl,
)

# TRACKING
from .tracking import (
    track_impression_impl,
    track_view_impl,
    track_share_impl,
)

# MANAGEMENT
from .management import (
    get_short_impl,
    my_shorts_impl,
    user_shorts_impl,
    delete_short_impl,
    retry_processing_impl,
)

# ANALYTICS
from .analytics import (
    get_short_analytics_impl,
    my_shorts_analytics_impl,
    user_short_analytics_impl,
    general_short_analytics_impl,
)

# SOUNDS
from .sounds import (
    init_sound_upload_impl,
    confirm_sound_upload_impl,
    list_sounds_impl,
    search_sounds_impl,
    get_sound_impl,
    favorite_sound_impl,
    my_favorite_sounds_impl,
    sound_shorts_impl,
    change_short_sound_impl,
    remove_short_sound_impl,
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


@frappe.whitelist(methods=["POST"])
def toggle_save_short(**kwargs):
    return toggle_save_short_impl(**kwargs)


@frappe.whitelist()
def saved_shorts(**kwargs):
    return saved_shorts_impl(**kwargs)


@frappe.whitelist()
def liked_shorts(**kwargs):
    return liked_shorts_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_repost(**kwargs):
    return toggle_repost_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def reposted_shorts(**kwargs):
    return reposted_shorts_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def download_short(**kwargs):
    return download_short_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def create_short_share_link(**kwargs):
    return create_short_share_link_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def share_short_to_chat(**kwargs):
    return share_short_to_chat_impl(**kwargs)


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


@frappe.whitelist(methods=["POST"])
def toggle_comment_like(**kwargs):
    return toggle_comment_like_impl(**kwargs)


# TRACKING
@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_impression(**kwargs):
    return track_impression_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_view(**kwargs):
    return track_view_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_share(**kwargs):
    return track_share_impl(**kwargs)


# MANAGEMENT
@frappe.whitelist(allow_guest=True)
def get_short(**kwargs):
    return get_short_impl(**kwargs)


@frappe.whitelist()
def my_shorts(**kwargs):
    return my_shorts_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def user_shorts(**kwargs):
    return user_shorts_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_short(**kwargs):
    return delete_short_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def retry_processing(**kwargs):
    return retry_processing_impl(**kwargs)


# ANALYTICS
@frappe.whitelist()
def get_short_analytics(**kwargs):
    return get_short_analytics_impl(**kwargs)


@frappe.whitelist()
def my_shorts_analytics(**kwargs):
    return my_shorts_analytics_impl(**kwargs)


@frappe.whitelist()
def user_short_analytics(**kwargs):
    return user_short_analytics_impl(**kwargs)


@frappe.whitelist()
def general_short_analytics(**kwargs):
    return general_short_analytics_impl(**kwargs)


# SOUNDS
@frappe.whitelist(methods=["POST"])
def init_sound_upload(**kwargs):
    return init_sound_upload_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def confirm_sound_upload(**kwargs):
    return confirm_sound_upload_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def list_sounds(**kwargs):
    return list_sounds_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def search_sounds(**kwargs):
    return search_sounds_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_sound(**kwargs):
    return get_sound_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def favorite_sound(**kwargs):
    return favorite_sound_impl(**kwargs)


@frappe.whitelist()
def my_favorite_sounds(**kwargs):
    return my_favorite_sounds_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def sound_shorts(**kwargs):
    return sound_shorts_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def change_short_sound(**kwargs):
    return change_short_sound_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def remove_short_sound(**kwargs):
    return remove_short_sound_impl(**kwargs)
