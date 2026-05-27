"""
Call endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

from __future__ import annotations
import frappe

# Call lifecycle
from .call import (
    initiate_call_impl,
    mark_call_ringing_impl,
    accept_call_impl,
    reject_call_impl,
    cancel_call_impl,
    end_call_impl,
)

# Status
from .status import (
    get_call_status_impl,
)

# Token
from .token import (
    get_call_token_impl,
)

# History
from .history import (
    list_calls_impl,
    get_call_group_details_impl,
    delete_call_logs_impl,
    clear_call_history_impl,
)


# Call lifecycle APIs
@frappe.whitelist(methods=["POST"])
def initiate_call(**kwargs):
    """Start an audio/video call for a conversation."""
    return initiate_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def mark_call_ringing(**kwargs):
    """Mark an incoming call as ringing on the receiver side."""
    return mark_call_ringing_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def accept_call(**kwargs):
    """Accept an incoming call."""
    return accept_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def reject_call(**kwargs):
    """Reject an incoming call."""
    return reject_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def cancel_call(**kwargs):
    """Cancel an outgoing call before it is accepted."""
    return cancel_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def end_call(**kwargs):
    """End an ongoing call."""
    return end_call_impl(**kwargs)


# Status API
@frappe.whitelist(methods=["GET", "POST"])
def get_call_status(**kwargs):
    """Get current call state."""
    return get_call_status_impl(**kwargs)


# Token API
@frappe.whitelist(methods=["POST"])
def get_call_token(**kwargs):
    """Generate a LiveKit token for reconnect/retry."""
    return get_call_token_impl(**kwargs)


# History APIs
@frappe.whitelist(methods=["GET", "POST"])
def list_calls(**kwargs):
    """List current user's grouped call history."""
    return list_calls_impl(**kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_call_group_details(**kwargs):
    """Get individual call logs inside a grouped call-history row."""
    return get_call_group_details_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_call_logs(**kwargs):
    """Delete one or more call logs for the current user only."""
    return delete_call_logs_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def clear_call_history(**kwargs):
    """Clear current user's visible call history only."""
    return clear_call_history_impl(**kwargs)
