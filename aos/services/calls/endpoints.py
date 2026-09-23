"""Strict field contracts for the existing public Calls v1 API."""

from __future__ import annotations

from .validation import CALL_ID_RE, CONVERSATION_ID_RE, EndpointSpec


def _spec(fields: set[str], *, ids=()) -> EndpointSpec:
    return EndpointSpec(frozenset(fields), tuple(ids))


ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "initiate_call": _spec({"participant_ids", "conversation_id", "call_type"}, ids=(("conversation_id", CONVERSATION_ID_RE),)),
    "mark_call_ringing": _spec({"call_id"}, ids=(("call_id", CALL_ID_RE),)),
    "accept_call": _spec({"call_id"}, ids=(("call_id", CALL_ID_RE),)),
    "reject_call": _spec({"call_id"}, ids=(("call_id", CALL_ID_RE),)),
    "cancel_call": _spec({"call_id"}, ids=(("call_id", CALL_ID_RE),)),
    "end_call": _spec({"call_id"}, ids=(("call_id", CALL_ID_RE),)),
    "add_call_participants": _spec({"call_id", "participant_ids"}, ids=(("call_id", CALL_ID_RE),)),
    "request_video_upgrade": _spec({"call_id"}, ids=(("call_id", CALL_ID_RE),)),
    "respond_video_upgrade": _spec({"call_id", "action"}, ids=(("call_id", CALL_ID_RE),)),
    "get_call_status": _spec({"call_id"}, ids=(("call_id", CALL_ID_RE),)),
    "get_call_token": _spec({"call_id"}, ids=(("call_id", CALL_ID_RE),)),
    "list_calls": _spec(
        {"limit", "conversation_id", "type", "cursor_created_at", "cursor_call_id"},
        ids=(("conversation_id", CONVERSATION_ID_RE), ("cursor_call_id", CALL_ID_RE)),
    ),
    "delete_call_logs": _spec({"call_ids"}),
    "clear_call_history": _spec(set()),
}

TRANSACTIONAL_ENDPOINTS = frozenset(
    {
        "initiate_call",
        "mark_call_ringing",
        "accept_call",
        "reject_call",
        "cancel_call",
        "end_call",
        "add_call_participants",
        "request_video_upgrade",
        "respond_video_upgrade",
        "get_call_token",
        "delete_call_logs",
        "clear_call_history",
    }
)
