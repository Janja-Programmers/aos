"""Canonical frontend/mobile Shorts API v1.

Video Processing, worker callbacks, reconciliation, and Desk moderation are internal
and intentionally absent from this namespace.
"""
from __future__ import annotations

import frappe

from aos.services.shorts.api import run_shorts_api
from aos.services.shorts.endpoints import ENDPOINT_SPECS, MUTATING_ENDPOINTS
from aos.services.shorts import service as _service


def _call(name: str, kwargs: dict):
    return run_shorts_api(
        getattr(_service, name),
        kwargs,
        spec=ENDPOINT_SPECS[name],
        operation_name=name,
        transactional=name in MUTATING_ENDPOINTS,
    )

@frappe.whitelist(methods=['POST'])
def create_short(**kwargs):
    """Execute the canonical Shorts `create_short` client operation."""
    return _call("create_short", kwargs)

@frappe.whitelist(methods=['POST'])
def update_short(**kwargs):
    """Execute the canonical Shorts `update_short` client operation."""
    return _call("update_short", kwargs)

@frappe.whitelist(methods=['POST'])
def submit_short(**kwargs):
    """Execute the canonical Shorts `submit_short` client operation."""
    return _call("submit_short", kwargs)

@frappe.whitelist(allow_guest=True, methods=['GET'])
def get_short(**kwargs):
    """Execute the canonical Shorts `get_short` client operation."""
    return _call("get_short", kwargs)

@frappe.whitelist(methods=['GET'])
def my_shorts(**kwargs):
    """Execute the canonical Shorts `my_shorts` client operation."""
    return _call("my_shorts", kwargs)

@frappe.whitelist(methods=['POST'])
def delete_short(**kwargs):
    """Execute the canonical Shorts `delete_short` client operation."""
    return _call("delete_short", kwargs)

@frappe.whitelist(methods=['POST'])
def retry_processing(**kwargs):
    """Execute the canonical Shorts `retry_processing` client operation."""
    return _call("retry_processing", kwargs)

@frappe.whitelist(allow_guest=True, methods=['GET'])
def feed_for_you(**kwargs):
    """Execute the canonical Shorts `feed_for_you` client operation."""
    return _call("feed_for_you", kwargs)

@frappe.whitelist(methods=['GET'])
def feed_following(**kwargs):
    """Execute the canonical Shorts `feed_following` client operation."""
    return _call("feed_following", kwargs)

@frappe.whitelist(methods=['POST'])
def like_short(**kwargs):
    """Execute the canonical Shorts `like_short` client operation."""
    return _call("like_short", kwargs)

@frappe.whitelist(methods=['POST'])
def unlike_short(**kwargs):
    """Execute the canonical Shorts `unlike_short` client operation."""
    return _call("unlike_short", kwargs)

@frappe.whitelist(methods=['POST'])
def save_short(**kwargs):
    """Execute the canonical Shorts `save_short` client operation."""
    return _call("save_short", kwargs)

@frappe.whitelist(methods=['POST'])
def unsave_short(**kwargs):
    """Execute the canonical Shorts `unsave_short` client operation."""
    return _call("unsave_short", kwargs)

@frappe.whitelist(methods=['GET'])
def saved_shorts(**kwargs):
    """Execute the canonical Shorts `saved_shorts` client operation."""
    return _call("saved_shorts", kwargs)

@frappe.whitelist(methods=['POST'])
def repost_short(**kwargs):
    """Execute the canonical Shorts `repost_short` client operation."""
    return _call("repost_short", kwargs)

@frappe.whitelist(methods=['POST'])
def undo_repost_short(**kwargs):
    """Execute the canonical Shorts `undo_repost_short` client operation."""
    return _call("undo_repost_short", kwargs)

@frappe.whitelist(methods=['POST'])
def not_interested(**kwargs):
    """Execute the canonical Shorts `not_interested` client operation."""
    return _call("not_interested", kwargs)

@frappe.whitelist(methods=['POST'])
def create_comment(**kwargs):
    """Execute the canonical Shorts `create_comment` client operation."""
    return _call("create_comment", kwargs)

@frappe.whitelist(methods=['POST'])
def delete_comment(**kwargs):
    """Execute the canonical Shorts `delete_comment` client operation."""
    return _call("delete_comment", kwargs)

@frappe.whitelist(allow_guest=True, methods=['GET'])
def list_comments(**kwargs):
    """Execute the canonical Shorts `list_comments` client operation."""
    return _call("list_comments", kwargs)

@frappe.whitelist(methods=['POST'])
def like_comment(**kwargs):
    """Execute the canonical Shorts `like_comment` client operation."""
    return _call("like_comment", kwargs)

@frappe.whitelist(methods=['POST'])
def unlike_comment(**kwargs):
    """Execute the canonical Shorts `unlike_comment` client operation."""
    return _call("unlike_comment", kwargs)

@frappe.whitelist(allow_guest=True, methods=['POST'])
def record_events(**kwargs):
    """Execute the canonical Shorts `record_events` client operation."""
    return _call("record_events", kwargs)

@frappe.whitelist(allow_guest=True, methods=['POST'])
def record_share(**kwargs):
    """Execute the canonical Shorts `record_share` client operation."""
    return _call("record_share", kwargs)

@frappe.whitelist(methods=['POST'])
def download_short(**kwargs):
    """Execute the canonical Shorts `download_short` client operation."""
    return _call("download_short", kwargs)

@frappe.whitelist(allow_guest=True, methods=['GET'])
def list_sounds(**kwargs):
    """Execute the canonical Shorts `list_sounds` client operation."""
    return _call("list_sounds", kwargs)

@frappe.whitelist(allow_guest=True, methods=['GET'])
def search_sounds(**kwargs):
    """Execute the canonical Shorts `search_sounds` client operation."""
    return _call("search_sounds", kwargs)

@frappe.whitelist(allow_guest=True, methods=['GET'])
def get_sound(**kwargs):
    """Execute the canonical Shorts `get_sound` client operation."""
    return _call("get_sound", kwargs)

@frappe.whitelist(allow_guest=True, methods=['GET'])
def sound_shorts(**kwargs):
    """Execute the canonical Shorts `sound_shorts` client operation."""
    return _call("sound_shorts", kwargs)

@frappe.whitelist(methods=['POST'])
def favorite_sound(**kwargs):
    """Execute the canonical Shorts `favorite_sound` client operation."""
    return _call("favorite_sound", kwargs)

@frappe.whitelist(methods=['POST'])
def unfavorite_sound(**kwargs):
    """Execute the canonical Shorts `unfavorite_sound` client operation."""
    return _call("unfavorite_sound", kwargs)

@frappe.whitelist(methods=['GET'])
def my_favorite_sounds(**kwargs):
    """Execute the canonical Shorts `my_favorite_sounds` client operation."""
    return _call("my_favorite_sounds", kwargs)

@frappe.whitelist(allow_guest=True, methods=['GET'])
def hashtag_shorts(**kwargs):
    """Execute the canonical Shorts `hashtag_shorts` client operation."""
    return _call("hashtag_shorts", kwargs)

@frappe.whitelist(methods=['POST'])
def create_side_by_side_draft(**kwargs):
    """Execute the canonical Shorts `create_side_by_side_draft` client operation."""
    return _call("create_side_by_side_draft", kwargs)

@frappe.whitelist(methods=['POST'])
def create_segment_reuse_draft(**kwargs):
    """Execute the canonical Shorts `create_segment_reuse_draft` client operation."""
    return _call("create_segment_reuse_draft", kwargs)

@frappe.whitelist(methods=['GET'])
def get_short_metrics(**kwargs):
    """Execute the canonical Shorts `get_short_metrics` client operation."""
    return _call("get_short_metrics", kwargs)
