"""Strict field contracts for the public Shorts v1 endpoints."""

from __future__ import annotations

from .validation import (
    ACCOUNT_ID_RE,
    CONVERSATION_ID_RE,
    MEDIA_ID_RE,
    SHORT_ID_RE,
    SOUND_ID_RE,
    EndpointSpec,
)


def _spec(fields: set[str], *, aliases=(), ids=()) -> EndpointSpec:
    return EndpointSpec(frozenset(fields), tuple(tuple(group) for group in aliases), tuple(ids))


ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "create_short": _spec(
        {
            "raw_video_media", "media_id", "media", "audience",
            "allow_comments", "allow_downloads", "sound_id",
            "sound_start_ms", "start_ms", "sound_duration_ms", "duration_ms",
            "sound_volume", "volume",
        },
        aliases=(
            ("raw_video_media", "media_id", "media"),
            ("sound_start_ms", "start_ms"),
            ("sound_duration_ms", "duration_ms"),
            ("sound_volume", "volume"),
        ),
        ids=(
            ("raw_video_media", MEDIA_ID_RE),
            ("media_id", MEDIA_ID_RE),
            ("media", MEDIA_ID_RE),
            ("sound_id", SOUND_ID_RE),
        ),
    ),
    "update_short_metadata": _spec(
        {"short_id", "content_mode", "caption", "hashtags", "audience", "allow_comments", "allow_downloads", "ad_id", "sound_id", "sound_start_ms", "start_ms", "sound_duration_ms", "duration_ms", "sound_volume", "volume"},
        aliases=(("sound_start_ms", "start_ms"), ("sound_duration_ms", "duration_ms"), ("sound_volume", "volume")),
        ids=(("short_id", SHORT_ID_RE), ("sound_id", SOUND_ID_RE)),
    ),
    "feed_for_you": _spec({"limit", "cursor", "content_mode", "mode"}, aliases=(("content_mode", "mode"),)),
    "feed_following": _spec({"limit", "cursor", "content_mode", "mode"}, aliases=(("content_mode", "mode"),)),
    "feed_by_ad": _spec({"ad_id", "limit", "cursor"}),
    "toggle_like": _spec({"short_id"}, ids=(("short_id", SHORT_ID_RE),)),
    "toggle_save_short": _spec({"short_id"}, ids=(("short_id", SHORT_ID_RE),)),
    "saved_shorts": _spec({"limit", "cursor"}),
    "liked_shorts": _spec({"limit", "cursor"}),
    "toggle_repost": _spec({"short_id", "note"}, ids=(("short_id", SHORT_ID_RE),)),
    "reposted_shorts": _spec({"user", "target_user", "limit", "cursor"}, aliases=(("user", "target_user"),)),
    "download_short": _spec({"short_id", "session_id", "event_id"}, ids=(("short_id", SHORT_ID_RE),)),
    "create_short_share_link": _spec({"short_id", "session_id", "channel", "event_id"}, ids=(("short_id", SHORT_ID_RE),)),
    "share_short_to_chat": _spec(
        {"short_id", "conversation_id", "message", "content", "event_id"},
        aliases=(("message", "content"),),
        ids=(("short_id", SHORT_ID_RE), ("conversation_id", CONVERSATION_ID_RE)),
    ),
    "add_comment": _spec({"short_id", "comment"}, ids=(("short_id", SHORT_ID_RE),)),
    "reply_comment": _spec({"parent_comment_id", "comment"}),
    "list_comments": _spec({"short_id", "limit", "cursor"}, ids=(("short_id", SHORT_ID_RE),)),
    "list_replies": _spec({"root_comment_id", "limit", "cursor"}),
    "delete_comment": _spec({"comment_id"}),
    "toggle_comment_like": _spec({"comment_id"}),
    "track_impression": _spec({"short_id", "session_id", "event_id"}, ids=(("short_id", SHORT_ID_RE),)),
    "track_view": _spec({"short_id", "session_id", "watch_ms", "event_id"}, ids=(("short_id", SHORT_ID_RE),)),
    "track_share": _spec({"short_id", "session_id", "channel", "source", "event_id"}, ids=(("short_id", SHORT_ID_RE),)),
    "get_short": _spec({"short_id"}, ids=(("short_id", SHORT_ID_RE),)),
    "my_shorts": _spec({"limit", "cursor", "scope"}),
    "user_shorts": _spec({"user", "target_user", "limit", "cursor", "content_mode", "mode"}, aliases=(("user", "target_user"), ("content_mode", "mode"))),
    "delete_short": _spec({"short_id"}, ids=(("short_id", SHORT_ID_RE),)),
    "retry_processing": _spec({"short_id"}, ids=(("short_id", SHORT_ID_RE),)),
    "get_short_analytics": _spec({"short_id", "date_from", "date_to"}, ids=(("short_id", SHORT_ID_RE),)),
    "my_shorts_analytics": _spec({"date_from", "date_to", "top_limit", "limit"}, aliases=(("top_limit", "limit"),)),
    "user_short_analytics": _spec({"user", "target_user", "date_from", "date_to", "top_limit", "limit"}, aliases=(("user", "target_user"), ("top_limit", "limit"))),
    "general_short_analytics": _spec({"date_from", "date_to", "top_limit", "limit"}, aliases=(("top_limit", "limit"),)),
    "create_sound": _spec(
        {"sound_media", "media_id", "media", "title", "artist", "source_type", "duration_seconds", "is_commercial_safe"},
        aliases=(("sound_media", "media_id", "media"),),
        ids=(("sound_media", MEDIA_ID_RE), ("media_id", MEDIA_ID_RE), ("media", MEDIA_ID_RE)),
    ),
    "list_sounds": _spec({"limit", "cursor", "source_type"}),
    "search_sounds": _spec({"q", "query", "limit"}, aliases=(("q", "query"),)),
    "get_sound": _spec({"sound_id"}, ids=(("sound_id", SOUND_ID_RE),)),
    "favorite_sound": _spec({"sound_id"}, ids=(("sound_id", SOUND_ID_RE),)),
    "my_favorite_sounds": _spec({"limit", "cursor"}),
    "sound_shorts": _spec({"sound_id", "limit", "cursor"}, ids=(("sound_id", SOUND_ID_RE),)),
    "change_short_sound": _spec(
        {"short_id", "sound_id", "sound_start_ms", "start_ms", "sound_duration_ms", "duration_ms", "sound_volume", "volume"},
        aliases=(("sound_start_ms", "start_ms"), ("sound_duration_ms", "duration_ms"), ("sound_volume", "volume")),
        ids=(("short_id", SHORT_ID_RE), ("sound_id", SOUND_ID_RE)),
    ),
    "remove_short_sound": _spec({"short_id"}, ids=(("short_id", SHORT_ID_RE),)),
}

MUTATING_ENDPOINTS = frozenset({
    "create_short", "update_short_metadata", "toggle_like", "toggle_save_short",
    "toggle_repost", "download_short", "create_short_share_link", "share_short_to_chat",
    "add_comment", "reply_comment", "delete_comment", "toggle_comment_like",
    "track_impression", "track_view", "track_share", "delete_short", "retry_processing",
    "create_sound", "favorite_sound", "change_short_sound", "remove_short_sound",
})
