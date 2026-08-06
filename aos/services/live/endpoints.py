"""Strict field contracts for the existing public Live v1 API."""

from __future__ import annotations

from .validation import ACCOUNT_REFERENCE_RE, LIVE_ID_RE, SAFE_ROW_ID_RE, EndpointSpec


def _spec(fields: set[str], *, aliases=(), ids=()) -> EndpointSpec:
    return EndpointSpec(frozenset(fields), tuple(tuple(g) for g in aliases), tuple(ids))


ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "start_live": _spec(
        {"title", "cover_image", "live_cover_media", "cover_image_media", "media_id"},
        aliases=(("live_cover_media", "cover_image_media", "media_id"),),
    ),
    "join_live": _spec({"live_id", "session_id"}, ids=(("live_id", LIVE_ID_RE),)),
    "end_live": _spec({"live_id"}, ids=(("live_id", LIVE_ID_RE),)),
    "get_live": _spec({"live_id", "session_id"}, ids=(("live_id", LIVE_ID_RE),)),
    "list_live_streams": _spec({"session_id", "limit", "start", "cursor"}),
    "get_live_token": _spec({"live_id", "session_id"}, ids=(("live_id", LIVE_ID_RE),)),
    "get_live_cohost_token": _spec({"cohost_id", "session_id"}, ids=(("cohost_id", SAFE_ROW_ID_RE),)),
    "track_join": _spec({"live_id", "session_id"}, ids=(("live_id", LIVE_ID_RE),)),
    "track_leave": _spec({"live_id", "session_id"}, ids=(("live_id", LIVE_ID_RE),)),
    "add_live_message": _spec({"live_id", "session_id", "content", "idempotency_key"}, ids=(("live_id", LIVE_ID_RE),)),
    "reply_live_message": _spec(
        {"live_id", "parent_message", "session_id", "content", "idempotency_key"},
        ids=(("live_id", LIVE_ID_RE), ("parent_message", SAFE_ROW_ID_RE)),
    ),
    "list_live_messages": _spec({"live_id", "limit", "start", "cursor"}, ids=(("live_id", LIVE_ID_RE),)),
    "list_live_replies": _spec({"parent_message", "limit", "start", "cursor"}, ids=(("parent_message", SAFE_ROW_ID_RE),)),
    "delete_live_message": _spec({"message_id"}, ids=(("message_id", SAFE_ROW_ID_RE),)),
    "send_reaction": _spec({"live_id", "reaction_type", "session_id"}, ids=(("live_id", LIVE_ID_RE),)),
    "invite_live_cohost": _spec(
        {"live_id", "target_user", "session_id"},
        ids=(("live_id", LIVE_ID_RE), ("target_user", ACCOUNT_REFERENCE_RE)),
    ),
    "request_live_cohost": _spec({"live_id", "session_id"}, ids=(("live_id", LIVE_ID_RE),)),
    "respond_live_cohost": _spec({"cohost_id", "action", "reason"}, ids=(("cohost_id", SAFE_ROW_ID_RE),)),
    "cancel_live_cohost": _spec({"cohost_id", "reason"}, ids=(("cohost_id", SAFE_ROW_ID_RE),)),
    "activate_live_cohost": _spec({"cohost_id", "session_id"}, ids=(("cohost_id", SAFE_ROW_ID_RE),)),
    "end_live_cohost": _spec({"cohost_id", "reason"}, ids=(("cohost_id", SAFE_ROW_ID_RE),)),
    "get_live_cohost": _spec({"cohost_id"}, ids=(("cohost_id", SAFE_ROW_ID_RE),)),
    "list_live_cohosts": _spec({"live_id", "status", "limit", "start", "cursor"}, ids=(("live_id", LIVE_ID_RE),)),
}

TRANSACTIONAL_ENDPOINTS = frozenset(
    {
        "start_live", "join_live", "end_live", "get_live_token", "get_live_cohost_token", "track_join", "track_leave", "add_live_message",
        "reply_live_message", "delete_live_message", "send_reaction", "invite_live_cohost",
        "request_live_cohost", "respond_live_cohost", "cancel_live_cohost",
        "activate_live_cohost", "end_live_cohost",
    }
)
