"""Authoritative call status/recovery endpoint."""
from __future__ import annotations

import frappe
from frappe.utils import get_datetime, now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.calls.livekit import call_rtc_ready
from aos.services.calls.participants import participant_for_user
from aos.services.calls.policy import ensure_call_interaction_allowed

from .constants import GET_CALL_STATUS_LIMIT_PER_MINUTE_PER_USER
from .realtime import serialize_call_for_realtime
from .validators import validate_call_exists, validate_user_in_call


def _build(call, current_user: str) -> dict:
    participant = participant_for_user(call.name, current_user)
    incoming = bool(
        participant and participant.role == "participant" and participant.status in {"invited", "ringing"}
        and participant.ring_expires_at and get_datetime(participant.ring_expires_at) > get_datetime(now_datetime())
        and int(call.is_active or 0) and call.status in {"initiated", "ringing", "ongoing"} and call_rtc_ready(call)
    )
    joined = bool(participant and participant.status == "joined")
    data = serialize_call_for_realtime(call, current_user=current_user)
    data.update({
        "can_show_incoming_ui": incoming,
        "can_accept": incoming,
        "can_join": joined and call_rtc_ready(call) and int(call.is_active or 0),
        "is_initiator": bool(participant and participant.role == "initiator"),
        "participant_status": participant.status if participant else None,
        "has_pending_video_upgrade_request": call.call_mode == "direct" and call.video_upgrade_status == "requested",
        "is_video_upgrade_requester": call.video_upgrade_requested_by == current_user,
        "can_request_video_upgrade": bool(call.call_mode == "direct" and joined and call.status == "ongoing" and call.call_type == "audio" and call.video_upgrade_status != "requested"),
        "can_respond_video_upgrade": bool(call.call_mode == "direct" and joined and call.status == "ongoing" and call.call_type == "audio" and call.video_upgrade_status == "requested" and call.video_upgrade_requested_by != current_user),
    })
    return data


def get_call_status_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "status", current_user), ttl_seconds=60, limit=GET_CALL_STATUS_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")):
        return rl
    call_id = kwargs.get("call_id")
    if not call_id:
        return fail("call_id is required.", error="VALIDATION_ERROR")
    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err
        if (membership := validate_user_in_call(call, current_user)):
            return membership
        if call.status in {"initiated", "ringing", "ongoing"} and int(call.is_active or 0):
            if (policy_error := ensure_call_interaction_allowed(call, current_user, action="access a call with")):
                return policy_error
        return ok("Call status fetched.", data=_build(call, current_user))
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Get Call Status Failed")
        return fail("Failed to fetch call status.", error="INTERNAL_ERROR")
