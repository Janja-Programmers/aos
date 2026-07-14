"""Public AOS API v1 wrappers for calls.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.calls.*.
Implementation stays in aos.api.calls implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.calls.call import (
    initiate_call_impl as _initiate_call_impl,
    mark_call_ringing_impl as _mark_call_ringing_impl,
    accept_call_impl as _accept_call_impl,
    reject_call_impl as _reject_call_impl,
    cancel_call_impl as _cancel_call_impl,
    end_call_impl as _end_call_impl,
    request_video_upgrade_impl as _request_video_upgrade_impl,
    respond_video_upgrade_impl as _respond_video_upgrade_impl,
)
from aos.api.calls.status import (
    get_call_status_impl as _get_call_status_impl,
)
from aos.api.calls.token import (
    get_call_token_impl as _get_call_token_impl,
)
from aos.api.calls.history import (
    list_calls_impl as _list_calls_impl,
    get_call_group_details_impl as _get_call_group_details_impl,
    delete_call_logs_impl as _delete_call_logs_impl,
    clear_call_history_impl as _clear_call_history_impl,
)

@frappe.whitelist(methods=["POST"])
def initiate_call(**kwargs):
    """Start an audio/video call for a conversation."""
    return _initiate_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def mark_call_ringing(**kwargs):
    """Mark an incoming call as ringing on the receiver side."""
    return _mark_call_ringing_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def accept_call(**kwargs):
    """Accept an incoming call."""
    return _accept_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def reject_call(**kwargs):
    """Reject an incoming call."""
    return _reject_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def cancel_call(**kwargs):
    """Cancel an outgoing call before it is accepted."""
    return _cancel_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def end_call(**kwargs):
    """End an ongoing call."""
    return _end_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def request_video_upgrade(**kwargs):
    """Request upgrading an ongoing audio call to video."""
    return _request_video_upgrade_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def respond_video_upgrade(**kwargs):
    """Accept or decline a pending audio-to-video upgrade request."""
    return _respond_video_upgrade_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_call_status(**kwargs):
    """Get current call state."""
    return _get_call_status_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def get_call_token(**kwargs):
    """Generate a LiveKit token for reconnect/retry."""
    return _get_call_token_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def list_calls(**kwargs):
    """List current user's grouped call history."""
    return _list_calls_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_call_group_details(**kwargs):
    """Get individual call logs inside a grouped call-history row."""
    return _get_call_group_details_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_call_logs(**kwargs):
    """Delete one or more call logs for the current user only."""
    return _delete_call_logs_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def clear_call_history(**kwargs):
    """Clear current user's visible call history only."""
    return _clear_call_history_impl(**kwargs)
