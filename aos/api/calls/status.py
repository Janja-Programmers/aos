"""
Call status endpoint.

Used by Flutter to validate call state before restoring incoming call UI from
terminated/background push payloads.

Example use case:
- Android receives an incoming call FCM while app is terminated
- Flutter boots from notification/call action
- Flutter calls get_call_status(call_id)
- Backend confirms whether the call is still valid to show/accept
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import GET_CALL_STATUS_LIMIT_PER_MINUTE_PER_USER

from .validators import (
    validate_call_exists,
    validate_user_in_call,
)

from .realtime import serialize_call_for_realtime


# HELPERS
def _is_truthy(value) -> bool:
    return bool(int(value or 0))


def _video_upgrade_status(call) -> str:
    return (getattr(call, "video_upgrade_status", None) or "none").strip().lower()


def _is_call_participant(*, call, current_user: str) -> bool:
    return current_user in (call.caller, call.receiver)


def _can_show_incoming_ui(*, call, current_user: str) -> bool:
    """
    Whether the receiver should still show native incoming call UI.

    This is mainly for terminated-app FCM reconstruction. It prevents Flutter
    from showing a stale incoming call if the caller already cancelled or the
    call already ended/missed/rejected.
    """
    return (
        current_user == call.receiver
        and call.status in ("initiated", "ringing")
        and _is_truthy(call.is_active)
    )


def _can_accept_call(*, call, current_user: str) -> bool:
    """
    Whether current user can accept this call.
    """
    return _can_show_incoming_ui(
        call=call,
        current_user=current_user,
    )


def _can_join_call(*, call, current_user: str) -> bool:
    """
    Whether current user can join/rejoin an ongoing call.

    Token generation remains handled by get_call_token; this endpoint only
    reports state.
    """
    return (
        _is_call_participant(call=call, current_user=current_user)
        and call.status == "ongoing"
        and _is_truthy(call.is_active)
    )


def _has_pending_video_upgrade_request(*, call) -> bool:
    return _video_upgrade_status(call) == "requested"


def _is_video_upgrade_requester(*, call, current_user: str) -> bool:
    return (
        _has_pending_video_upgrade_request(call=call)
        and call.video_upgrade_requested_by == current_user
    )


def _can_request_video_upgrade(*, call, current_user: str) -> bool:
    """
    Whether current user can request audio -> video upgrade.

    Rules:
    - user must be a call participant
    - call must be ongoing
    - call must still be audio
    - no pending video upgrade request exists
    """
    return (
        _is_call_participant(call=call, current_user=current_user)
        and call.status == "ongoing"
        and _is_truthy(call.is_active)
        and call.call_type == "audio"
        and not _has_pending_video_upgrade_request(call=call)
    )


def _can_respond_video_upgrade(*, call, current_user: str) -> bool:
    """
    Whether current user can accept/decline pending video upgrade.

    The requester cannot respond to their own request.
    """
    return (
        _is_call_participant(call=call, current_user=current_user)
        and call.status == "ongoing"
        and _is_truthy(call.is_active)
        and call.call_type == "audio"
        and _has_pending_video_upgrade_request(call=call)
        and call.video_upgrade_requested_by
        and call.video_upgrade_requested_by != current_user
    )


def _build_call_status_response(*, call, current_user: str) -> dict:
    """
    Build display-ready call status payload.

    Reuses the same serializer used by realtime/API responses so Flutter sees
    a consistent call shape across:
    - realtime
    - initiate/accept responses
    - FCM reconstruction validation
    """
    has_pending_video_upgrade_request = _has_pending_video_upgrade_request(
        call=call,
    )

    data = serialize_call_for_realtime(
        call,
        current_user=current_user,
    )

    data.update(
        {
            "can_show_incoming_ui": _can_show_incoming_ui(
                call=call,
                current_user=current_user,
            ),
            "can_accept": _can_accept_call(
                call=call,
                current_user=current_user,
            ),
            "can_join": _can_join_call(
                call=call,
                current_user=current_user,
            ),
            "is_receiver": current_user == call.receiver,
            "is_caller": current_user == call.caller,
            "has_pending_video_upgrade_request": has_pending_video_upgrade_request,
            "is_video_upgrade_requester": _is_video_upgrade_requester(
                call=call,
                current_user=current_user,
            ),
            "can_request_video_upgrade": _can_request_video_upgrade(
                call=call,
                current_user=current_user,
            ),
            "can_respond_video_upgrade": _can_respond_video_upgrade(
                call=call,
                current_user=current_user,
            ),
        }
    )

    return data


# GET CALL STATUS
def get_call_status_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:calls:status:user:{current_user}",
        ttl_seconds=60,
        limit=GET_CALL_STATUS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    call_id = kwargs.get("call_id") or kwargs.get("id")

    if not call_id:
        return fail("call_id is required.", code="VALIDATION_ERROR")

    try:
        call, err = validate_call_exists(call_id)
        if err:
            return err

        err = validate_user_in_call(call, current_user)
        if err:
            return err

        return ok(
            "Call status fetched.",
            data=_build_call_status_response(
                call=call,
                current_user=current_user,
            ),
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Call Status Failed",
        )
        return fail("Failed to fetch call status.", code="INTERNAL_ERROR")
