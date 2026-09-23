"""Realtime payloads/events for direct and conference calls."""
from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.user_display import get_user_display, get_user_display_map
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.calls.identifiers import public_call_id
from aos.services.calls.livekit import call_rtc_ready
from aos.services.calls.participants import participant_rows, users_for_call


def _summary(user: str | None, display_map: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not user:
        return None
    if user in display_map:
        return display_map[user]
    try:
        return get_user_display(user)
    except Exception:
        return {
            "account_id": public_account_id_for_user(user), "display_name": "AOS User", "avatar": None,
            "is_deleted": False, "is_live": False, "live_id": None, "live_status": None,
        }


def serialize_call_for_realtime(call, *, current_user: str | None = None, event_status: str | None = None, actor: str | None = None, user_summaries=None, participants_override=None) -> dict[str, Any]:
    rows = participants_override if participants_override is not None else participant_rows(call.name)
    users = {row.user for row in rows if row.user}
    users.update(x for x in (actor, call.initiator, getattr(call, "ended_by", None), getattr(call, "video_upgrade_requested_by", None)) if x)
    if user_summaries is None:
        try:
            user_summaries = get_user_display_map(users)
        except Exception:
            user_summaries = {}
    participants = []
    current = None
    for row in rows:
        info = _summary(row.user, user_summaries) or {}
        item = {
            "account_id": info.get("account_id"),
            "display_name": info.get("display_name") or "AOS User",
            "avatar": info.get("avatar"),
            "is_deleted": bool(info.get("is_deleted")),
            "role": row.role,
            "status": row.status,
            "invited_at": row.invited_at,
            "ringing_at": row.ringing_at,
            "joined_at": row.joined_at,
            "left_at": row.left_at,
        }
        participants.append(item)
        if current_user == row.user:
            current = item
    initiator = _summary(call.initiator, user_summaries) or {}
    actor_info = _summary(actor, user_summaries) if actor else None
    ended = _summary(getattr(call, "ended_by", None), user_summaries) if getattr(call, "ended_by", None) else None
    requester = _summary(getattr(call, "video_upgrade_requested_by", None), user_summaries) if getattr(call, "video_upgrade_requested_by", None) else None
    return {
        "call_id": public_call_id(call),
        "conversation_id": call.conversation or None,
        "call_mode": call.call_mode,
        "call_type": call.call_type,
        "status": call.status,
        "event_status": event_status or call.status,
        "state_version": int(call.state_version or 1),
        "rtc_ready": call_rtc_ready(call),
        "is_active": bool(call.is_active),
        "max_participants": int(call.max_participants or (2 if call.call_mode == "direct" else 32)),
        "participant_count": int(call.participant_count or len(rows)),
        "initiator": initiator.get("account_id"),
        "initiator_display_name": initiator.get("display_name") or "AOS User",
        "initiator_avatar": initiator.get("avatar"),
        "participants": participants,
        "current_participant_status": current.get("status") if current else None,
        "actor": actor_info.get("account_id") if actor_info else None,
        "actor_display_name": actor_info.get("display_name") if actor_info else None,
        "video_upgrade_status": getattr(call, "video_upgrade_status", None) or "none",
        "video_upgrade_requested_by": requester.get("account_id") if requester else None,
        "video_upgrade_requested_at": getattr(call, "video_upgrade_requested_at", None),
        "video_upgrade_responded_at": getattr(call, "video_upgrade_responded_at", None),
        "ringing_at": call.ringing_at,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "ended_by": ended.get("account_id") if ended else None,
        "duration": int(call.duration or 0),
    }


def _publish_for_users(event: str, call, users: list[str], *, event_status: str, actor: str | None = None):
    for user in sorted(set(users)):
        if not user:
            continue
        frappe.publish_realtime(
            event=event,
            message=serialize_call_for_realtime(call, current_user=user, event_status=event_status, actor=actor),
            user=user,
            after_commit=True,
        )


def publish_call_ready(call):
    _publish_for_users("aos_call_ready", call, [call.initiator], event_status="ready", actor=call.initiator)


def publish_call_failed_to_initiator(call):
    _publish_for_users("aos_call_ended", call, [call.initiator], event_status="failed", actor=None)


def publish_incoming_call(call, user: str, *, actor: str | None = None):
    _publish_for_users("aos_incoming_call", call, [user], event_status="incoming", actor=actor or call.initiator)


def _joined_plus(call, *extra: str) -> list[str]:
    return list(dict.fromkeys([*users_for_call(call.name, statuses=("joined",)), *[u for u in extra if u]]))


def publish_participants_invited(call, *, actor: str):
    _publish_for_users(
        "aos_call_participants_invited", call, _joined_plus(call),
        event_status="participants_invited", actor=actor,
    )


def publish_participant_ringing(call, user: str):
    _publish_for_users(
        "aos_call_participant_ringing", call, _joined_plus(call, user),
        event_status="participant_ringing", actor=user,
    )


def publish_participant_joined(call, user: str):
    _publish_for_users(
        "aos_call_participant_joined", call, _joined_plus(call, user),
        event_status="participant_joined", actor=user,
    )


def publish_participant_declined(call, user: str):
    _publish_for_users(
        "aos_call_participant_declined", call, _joined_plus(call, user),
        event_status="participant_declined", actor=user,
    )


def publish_participant_left(call, user: str):
    _publish_for_users(
        "aos_call_participant_left", call, _joined_plus(call, user),
        event_status="participant_left", actor=user,
    )


def publish_participant_missed(call, user: str):
    _publish_for_users(
        "aos_call_participant_missed", call, _joined_plus(call, user),
        event_status="participant_missed", actor=user,
    )


def publish_call_cancelled(call):
    _publish_for_users("aos_call_cancelled", call, users_for_call(call.name), event_status="cancelled", actor=call.initiator)


def publish_call_ended(call, *, event_status: str = "ended"):
    _publish_for_users("aos_call_ended", call, users_for_call(call.name), event_status=event_status, actor=call.ended_by)


def publish_video_upgrade_requested(call):
    if call.call_mode != "direct" or not call.video_upgrade_requested_by:
        return
    targets = [u for u in users_for_call(call.name, statuses=("joined",)) if u != call.video_upgrade_requested_by]
    _publish_for_users("aos_call_video_upgrade_requested", call, targets, event_status="video_upgrade_requested", actor=call.video_upgrade_requested_by)


def publish_video_upgrade_accepted(call, actor: str | None = None):
    _publish_for_users("aos_call_video_upgrade_accepted", call, users_for_call(call.name, statuses=("joined",)), event_status="video_upgrade_accepted", actor=actor)


def publish_video_upgrade_declined(call, actor: str | None = None):
    requester = call.video_upgrade_requested_by
    if requester:
        _publish_for_users("aos_call_video_upgrade_declined", call, [requester], event_status="video_upgrade_declined", actor=actor)
