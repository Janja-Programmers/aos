"""Public AOS API v1 wrappers for shorts.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.shorts.*.
Implementation stays in aos.api.shorts implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.shorts.upload import (
    create_short_impl as _create_short_impl,
    update_short_metadata_impl as _update_short_metadata_impl,
)
from aos.api.shorts.feed import (
    feed_for_you_impl as _feed_for_you_impl,
    feed_following_impl as _feed_following_impl,
    feed_by_ad_impl as _feed_by_ad_impl,
)
from aos.api.shorts.recommendation import (
    recommendation_feedback_impl as _recommendation_feedback_impl,
)
from aos.api.shorts.engagement import (
    toggle_like_impl as _toggle_like_impl,
)
from aos.api.shorts.library import (
    toggle_save_short_impl as _toggle_save_short_impl,
    saved_shorts_impl as _saved_shorts_impl,
    liked_shorts_impl as _liked_shorts_impl,
    toggle_repost_impl as _toggle_repost_impl,
    reposted_shorts_impl as _reposted_shorts_impl,
    download_short_impl as _download_short_impl,
)
from aos.api.shorts.share import (
    create_short_share_link_impl as _create_short_share_link_impl,
    share_short_to_chat_impl as _share_short_to_chat_impl,
)
from aos.api.shorts.comments import (
    add_comment_impl as _add_comment_impl,
    reply_comment_impl as _reply_comment_impl,
    list_comments_impl as _list_comments_impl,
    list_replies_impl as _list_replies_impl,
    delete_comment_impl as _delete_comment_impl,
    toggle_comment_like_impl as _toggle_comment_like_impl,
)
from aos.api.shorts.tracking import (
    track_impression_impl as _track_impression_impl,
    track_view_impl as _track_view_impl,
    track_share_impl as _track_share_impl,
)
from aos.api.shorts.management import (
    get_short_impl as _get_short_impl,
    my_shorts_impl as _my_shorts_impl,
    user_shorts_impl as _user_shorts_impl,
    delete_short_impl as _delete_short_impl,
    retry_processing_impl as _retry_processing_impl,
)
from aos.api.shorts.analytics import (
    get_short_analytics_impl as _get_short_analytics_impl,
    my_shorts_analytics_impl as _my_shorts_analytics_impl,
    user_short_analytics_impl as _user_short_analytics_impl,
    general_short_analytics_impl as _general_short_analytics_impl,
)
from aos.api.shorts.sounds import (
    create_sound_impl as _create_sound_impl,
    list_sounds_impl as _list_sounds_impl,
    search_sounds_impl as _search_sounds_impl,
    get_sound_impl as _get_sound_impl,
    favorite_sound_impl as _favorite_sound_impl,
    my_favorite_sounds_impl as _my_favorite_sounds_impl,
    sound_shorts_impl as _sound_shorts_impl,
    change_short_sound_impl as _change_short_sound_impl,
    remove_short_sound_impl as _remove_short_sound_impl,
)

from aos.services.shorts.api import run_shorts_api
from aos.services.shorts.endpoints import ENDPOINT_SPECS, MUTATING_ENDPOINTS


def _call(name, implementation, kwargs):
    return run_shorts_api(
        implementation,
        kwargs,
        spec=ENDPOINT_SPECS[name],
        operation_name=name,
        transactional=name in MUTATING_ENDPOINTS,
    )

@frappe.whitelist(methods=["POST"])
def create_short(**kwargs):
    """Execute the v1 shorts.create_short endpoint."""
    return _call("create_short", _create_short_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def update_short_metadata(**kwargs):
    """Execute the v1 shorts.update_short_metadata endpoint."""
    return _call("update_short_metadata", _update_short_metadata_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def feed_for_you(**kwargs):
    """Execute the v1 shorts.feed_for_you endpoint."""
    return _call("feed_for_you", _feed_for_you_impl, kwargs)


@frappe.whitelist()
def feed_following(**kwargs):
    """Execute the v1 shorts.feed_following endpoint."""
    return _call("feed_following", _feed_following_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def feed_by_ad(**kwargs):
    """Execute the v1 shorts.feed_by_ad endpoint."""
    return _call("feed_by_ad", _feed_by_ad_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def recommendation_feedback(**kwargs):
    """Execute the v1 shorts.recommendation_feedback endpoint."""
    return _call("recommendation_feedback", _recommendation_feedback_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_like(**kwargs):
    """Execute the v1 shorts.toggle_like endpoint."""
    return _call("toggle_like", _toggle_like_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_save_short(**kwargs):
    """Execute the v1 shorts.toggle_save_short endpoint."""
    return _call("toggle_save_short", _toggle_save_short_impl, kwargs)


@frappe.whitelist()
def saved_shorts(**kwargs):
    """Execute the v1 shorts.saved_shorts endpoint."""
    return _call("saved_shorts", _saved_shorts_impl, kwargs)


@frappe.whitelist()
def liked_shorts(**kwargs):
    """Execute the v1 shorts.liked_shorts endpoint."""
    return _call("liked_shorts", _liked_shorts_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_repost(**kwargs):
    """Execute the v1 shorts.toggle_repost endpoint."""
    return _call("toggle_repost", _toggle_repost_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def reposted_shorts(**kwargs):
    """Execute the v1 shorts.reposted_shorts endpoint."""
    return _call("reposted_shorts", _reposted_shorts_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def download_short(**kwargs):
    """Execute the v1 shorts.download_short endpoint."""
    return _call("download_short", _download_short_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def create_short_share_link(**kwargs):
    """Execute the v1 shorts.create_short_share_link endpoint."""
    return _call("create_short_share_link", _create_short_share_link_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def share_short_to_chat(**kwargs):
    """Execute the v1 shorts.share_short_to_chat endpoint."""
    return _call("share_short_to_chat", _share_short_to_chat_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def add_comment(**kwargs):
    """Execute the v1 shorts.add_comment endpoint."""
    return _call("add_comment", _add_comment_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def reply_comment(**kwargs):
    """Execute the v1 shorts.reply_comment endpoint."""
    return _call("reply_comment", _reply_comment_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def list_comments(**kwargs):
    """Execute the v1 shorts.list_comments endpoint."""
    return _call("list_comments", _list_comments_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def list_replies(**kwargs):
    """Execute the v1 shorts.list_replies endpoint."""
    return _call("list_replies", _list_replies_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def delete_comment(**kwargs):
    """Execute the v1 shorts.delete_comment endpoint."""
    return _call("delete_comment", _delete_comment_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_comment_like(**kwargs):
    """Execute the v1 shorts.toggle_comment_like endpoint."""
    return _call("toggle_comment_like", _toggle_comment_like_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_impression(**kwargs):
    """Execute the v1 shorts.track_impression endpoint."""
    return _call("track_impression", _track_impression_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_view(**kwargs):
    """Execute the v1 shorts.track_view endpoint."""
    return _call("track_view", _track_view_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def track_share(**kwargs):
    """Execute the v1 shorts.track_share endpoint."""
    return _call("track_share", _track_share_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def get_short(**kwargs):
    """Execute the v1 shorts.get_short endpoint."""
    return _call("get_short", _get_short_impl, kwargs)


@frappe.whitelist()
def my_shorts(**kwargs):
    """Execute the v1 shorts.my_shorts endpoint."""
    return _call("my_shorts", _my_shorts_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def user_shorts(**kwargs):
    """Execute the v1 shorts.user_shorts endpoint."""
    return _call("user_shorts", _user_shorts_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def delete_short(**kwargs):
    """Execute the v1 shorts.delete_short endpoint."""
    return _call("delete_short", _delete_short_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def retry_processing(**kwargs):
    """Execute the v1 shorts.retry_processing endpoint."""
    return _call("retry_processing", _retry_processing_impl, kwargs)


@frappe.whitelist()
def get_short_analytics(**kwargs):
    """Execute the v1 shorts.get_short_analytics endpoint."""
    return _call("get_short_analytics", _get_short_analytics_impl, kwargs)


@frappe.whitelist()
def my_shorts_analytics(**kwargs):
    """Execute the v1 shorts.my_shorts_analytics endpoint."""
    return _call("my_shorts_analytics", _my_shorts_analytics_impl, kwargs)


@frappe.whitelist()
def user_short_analytics(**kwargs):
    """Execute the v1 shorts.user_short_analytics endpoint."""
    return _call("user_short_analytics", _user_short_analytics_impl, kwargs)


@frappe.whitelist()
def general_short_analytics(**kwargs):
    """Execute the v1 shorts.general_short_analytics endpoint."""
    return _call("general_short_analytics", _general_short_analytics_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def create_sound(**kwargs):
    """Execute the v1 shorts.create_sound endpoint."""
    return _call("create_sound", _create_sound_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def list_sounds(**kwargs):
    """Execute the v1 shorts.list_sounds endpoint."""
    return _call("list_sounds", _list_sounds_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def search_sounds(**kwargs):
    """Execute the v1 shorts.search_sounds endpoint."""
    return _call("search_sounds", _search_sounds_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def get_sound(**kwargs):
    """Execute the v1 shorts.get_sound endpoint."""
    return _call("get_sound", _get_sound_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def favorite_sound(**kwargs):
    """Execute the v1 shorts.favorite_sound endpoint."""
    return _call("favorite_sound", _favorite_sound_impl, kwargs)


@frappe.whitelist()
def my_favorite_sounds(**kwargs):
    """Execute the v1 shorts.my_favorite_sounds endpoint."""
    return _call("my_favorite_sounds", _my_favorite_sounds_impl, kwargs)


@frappe.whitelist(allow_guest=True)
def sound_shorts(**kwargs):
    """Execute the v1 shorts.sound_shorts endpoint."""
    return _call("sound_shorts", _sound_shorts_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def change_short_sound(**kwargs):
    """Execute the v1 shorts.change_short_sound endpoint."""
    return _call("change_short_sound", _change_short_sound_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def remove_short_sound(**kwargs):
    """Execute the v1 shorts.remove_short_sound endpoint."""
    return _call("remove_short_sound", _remove_short_sound_impl, kwargs)
