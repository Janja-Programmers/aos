"""
Live Stream validators.

Reusable validation helpers for the social Live feature.

Responsibilities:
- Live stream validation
- Host and go-live eligibility validation
- Viewer-session validation
- Live participant validation
- Co-host candidate and workflow validation
"""

from __future__ import annotations

import frappe
from frappe.utils import get_datetime, now_datetime

from aos.api.shared.blocking import is_blocked_between
from aos.api.shared.responses import fail

from .constants import (
    LIVE_COHOST_MAX_ACTIVE_SLOTS,
)


LIVE_STREAM_DOCTYPE = "AOS Live Stream"
LIVE_VIEW_DOCTYPE = "AOS Live Stream View"
LIVE_COHOST_DOCTYPE = "AOS Live CoHost"
USER_DOCTYPE = "User"

GUEST_USER = "Guest"

LIVE_STATUS = "live"
ENDED_STATUS = "ended"

COHOST_REQUEST_TYPE_HOST_INVITE = "host_invite"
COHOST_REQUEST_TYPE_VIEWER_REQUEST = "viewer_request"

COHOST_STATUS_PENDING = "pending"
COHOST_STATUS_ACCEPTED = "accepted"
COHOST_STATUS_REJECTED = "rejected"
COHOST_STATUS_CANCELLED = "cancelled"
COHOST_STATUS_ACTIVE = "active"
COHOST_STATUS_ENDED = "ended"
COHOST_STATUS_EXPIRED = "expired"

COHOST_UNRESOLVED_STATUSES = {
    COHOST_STATUS_PENDING,
    COHOST_STATUS_ACCEPTED,
    COHOST_STATUS_ACTIVE,
}

COHOST_SLOT_RESERVED_STATUSES = {
    COHOST_STATUS_ACCEPTED,
    COHOST_STATUS_ACTIVE,
}

COHOST_TERMINAL_STATUSES = {
    COHOST_STATUS_REJECTED,
    COHOST_STATUS_CANCELLED,
    COHOST_STATUS_ENDED,
    COHOST_STATUS_EXPIRED,
}


# GENERIC HELPERS
def is_authenticated_user(
    user: str | None,
) -> bool:
    return bool(
        user
        and user != GUEST_USER
    )


def normalize_session_id(
    value,
) -> str | None:
    session_id = str(
        value or ""
    ).strip()

    return session_id or None


def _apply_viewer_ownership_filter(
    *,
    filters: dict,
    user: str | None,
):
    """
    Constrain a viewer-session query to its real owner.

    Authenticated viewer:
    - user must match exactly.

    Guest viewer:
    - the view row must have no user.
    """
    if is_authenticated_user(user):
        filters["user"] = user
    else:
        filters["user"] = [
            "is",
            "not set",
        ]


# FETCH HELPERS
def get_live_row(
    live_id: str,
):
    """
    Return an AOS Live Stream document or None.
    """
    if not live_id:
        return None

    try:
        return frappe.get_doc(
            LIVE_STREAM_DOCTYPE,
            live_id,
        )
    except frappe.DoesNotExistError:
        return None


def get_live_view_row(
    view_id: str,
):
    """
    Return an AOS Live Stream View document or None.
    """
    if not view_id:
        return None

    try:
        return frappe.get_doc(
            LIVE_VIEW_DOCTYPE,
            view_id,
        )
    except frappe.DoesNotExistError:
        return None


def get_live_cohost_row(
    cohost_id: str,
):
    """
    Return an AOS Live CoHost document or None.
    """
    if not cohost_id:
        return None

    try:
        return frappe.get_doc(
            LIVE_COHOST_DOCTYPE,
            cohost_id,
        )
    except frappe.DoesNotExistError:
        return None


def get_enabled_user_row(
    user: str,
):
    """
    Return an enabled User row or an error response.
    """
    if not is_authenticated_user(user):
        return None, fail(
            "Login required.",
            error="AUTH_REQUIRED",
        )

    user_row = frappe.db.get_value(
        USER_DOCTYPE,
        user,
        [
            "name",
            "enabled",
        ],
        as_dict=True,
    )

    if not user_row:
        return None, fail(
            "User not found.",
            error="NOT_FOUND",
        )

    if not bool(user_row.enabled):
        return None, fail(
            "User account is disabled.",
            error="INVALID_STATE",
        )

    return user_row, None


# LIVE VALIDATION
def validate_live_exists(
    live_id: str,
):
    """
    Validate that a live stream exists.

    Returns:
        tuple[live | None, error_response | None]
    """
    live = get_live_row(
        live_id
    )

    if not live:
        return None, fail(
            "Live stream not found.",
            error="NOT_FOUND",
        )

    return live, None


def validate_live_active(
    live,
):
    """
    Validate that a live stream is currently active.
    """
    if (
        live.status != LIVE_STATUS
        or not bool(live.is_active)
    ):
        return fail(
            "Live stream is not active.",
            error="INVALID_STATE",
        )

    return None


def validate_live_social_access(
    *,
    live,
    user: str | None,
):
    """Hide a host/live relationship when either account has blocked the other."""
    if not is_authenticated_user(user) or live.host_user == user:
        return None
    if is_blocked_between(user, live.host_user):
        return fail(
            "Live stream not found.",
            error="NOT_FOUND",
        )
    return None


def validate_live_not_ended(
    live,
):
    """
    Validate that the live stream has not ended.
    """
    if live.status == ENDED_STATUS:
        return fail(
            "Live stream has ended.",
            error="INVALID_STATE",
        )

    return None


def validate_user_is_host(
    live,
    user: str,
):
    """
    Validate that the supplied user owns the live stream.
    """
    if not is_authenticated_user(user):
        return fail(
            "Login required.",
            error="AUTH_REQUIRED",
        )

    if live.host_user != user:
        return fail(
            "Only the host can perform this action.",
            error="PERMISSION_DENIED",
        )

    return None


def validate_user_can_go_live(
    user: str,
):
    """
    Validate whether a user can start a live stream.

    Current rules:
    - Login is required.
    - User must exist.
    - User must be enabled.

    Future eligibility rules can be added here without changing start_live.
    """
    return get_enabled_user_row(
        user
    )


# VIEW-SESSION VALIDATION
def validate_view_identity(
    user: str | None,
    session_id: str | None,
):
    """
    Validate a viewer tracking identity.

    session_id is mandatory for:
    - authenticated viewers
    - guest viewers

    Authenticated users are also checked for existence and enabled status.
    """
    session_id = normalize_session_id(
        session_id
    )

    if not session_id:
        return fail(
            "Session id is required.",
            error="VALIDATION_ERROR",
        )

    if not is_authenticated_user(user):
        return None

    _, err = get_enabled_user_row(
        user
    )

    return err


def validate_no_active_view_session(
    live_id: str,
    user: str | None,
    session_id: str | None,
):
    """
    Ensure this exact viewer/session does not already have an active row.
    """
    session_id = normalize_session_id(
        session_id
    )

    if not session_id:
        return fail(
            "Session id is required.",
            error="VALIDATION_ERROR",
        )

    filters = {
        "live_stream": live_id,
        "session_id": session_id,
        "is_active": 1,
    }

    _apply_viewer_ownership_filter(
        filters=filters,
        user=user,
    )

    exists = frappe.db.exists(
        LIVE_VIEW_DOCTYPE,
        filters,
    )

    if exists:
        return fail(
            "Active view session already exists.",
            error="INVALID_STATE",
        )

    return None


def validate_active_view_session(
    live_id: str,
    user: str | None,
    session_id: str | None,
):
    """
    Fetch an active view session owned by the supplied viewer.

    Authenticated viewer:
    - session must belong to the exact user.

    Guest viewer:
    - session must belong to a row with no user.
    """
    session_id = normalize_session_id(
        session_id
    )

    if not session_id:
        return None, fail(
            "Session id is required.",
            error="VALIDATION_ERROR",
        )

    filters = {
        "live_stream": live_id,
        "session_id": session_id,
        "is_active": 1,
    }

    _apply_viewer_ownership_filter(
        filters=filters,
        user=user,
    )

    view = frappe.db.get_value(
        LIVE_VIEW_DOCTYPE,
        filters,
        [
            "name",
            "live_stream",
            "user",
            "session_id",
            "is_active",
            "joined_at",
            "left_at",
            "last_seen_at",
        ],
        as_dict=True,
    )

    if not view:
        return None, fail(
            "Active view session not found.",
            error="NOT_FOUND",
        )

    return view, None


def validate_live_participant_session(
    *,
    live,
    user: str,
    session_id: str | None,
):
    """
    Validate that a logged-in user may interact with the live.

    Host:
    - Does not require an AOS Live Stream View session.

    Non-host viewer:
    - Must provide session_id.
    - Must own an active view session for this live.

    Used by:
    - comments
    - replies
    - reactions
    - future viewer-side Live actions
    """
    if not is_authenticated_user(user):
        return fail(
            "Login required.",
            error="AUTH_REQUIRED",
        )

    if live.host_user == user:
        return None

    blocked_err = validate_live_social_access(live=live, user=user)
    if blocked_err:
        return blocked_err

    session_id = normalize_session_id(
        session_id
    )

    if not session_id:
        return fail(
            "session_id is required for viewers.",
            error="VALIDATION_ERROR",
        )

    _, err = validate_active_view_session(
        live.name,
        user,
        session_id,
    )

    return err


# CO-HOST FETCHING
def validate_cohost_exists(
    cohost_id: str,
):
    """
    Validate that a co-host workflow record exists.
    """

    cohost = get_live_cohost_row(
        cohost_id
    )

    if not cohost:
        return None, fail(
            "Co-host request not found.",
            error="NOT_FOUND",
        )

    return cohost, None


def validate_cohost_belongs_to_live(
    cohost,
    live_id: str,
):
    """
    Validate that the co-host workflow belongs to the expected live.
    """
    if cohost.live_stream != live_id:
        return fail(
            "Co-host request does not belong to this live stream.",
            error="INVALID_STATE",
        )

    return None


# CO-HOST CANDIDATE VALIDATION
def validate_user_is_active_viewer(
    *,
    live,
    user: str,
    session_id: str | None,
):
    """
    Validate an authenticated active viewer session.

    Unlike validate_live_participant_session(), this never permits the host
    because the host cannot be a co-host candidate.
    """
    if not is_authenticated_user(user):
        return None, fail(
            "A logged-in viewer is required.",
            error="AUTH_REQUIRED",
        )

    if live.host_user == user:
        return None, fail(
            "The live host cannot be a co-host candidate.",
            error="VALIDATION_ERROR",
        )

    blocked_err = validate_live_social_access(live=live, user=user)
    if blocked_err:
        return None, blocked_err

    _, err = get_enabled_user_row(
        user
    )
    if err:
        return None, err

    session_id = normalize_session_id(
        session_id
    )

    if not session_id:
        return None, fail(
            "session_id is required for the co-host candidate.",
            error="VALIDATION_ERROR",
        )

    view, err = validate_active_view_session(
        live.name,
        user,
        session_id,
    )

    if err:
        return None, fail(
            "The co-host candidate must be actively watching this live.",
            error="INVALID_STATE",
        )

    return view, None


def validate_user_is_cohost_candidate(
    *,
    live,
    user: str,
    session_id: str | None,
):
    """
    Validate that a user is eligible to enter a co-host workflow.
    """
    return validate_user_is_active_viewer(
        live=live,
        user=user,
        session_id=session_id,
    )


# CO-HOST WORKFLOW STATE
def validate_cohost_pending(
    cohost,
):
    """
    Require a pending co-host request or invitation.
    """
    if cohost.status != COHOST_STATUS_PENDING:
        return fail(
            "Co-host request is no longer pending.",
            error="INVALID_STATE",
        )

    return None


def validate_cohost_accepted(
    cohost,
):
    """
    Require an accepted co-host request before activation.
    """
    if cohost.status != COHOST_STATUS_ACCEPTED:
        return fail(
            "Co-host request has not been accepted.",
            error="INVALID_STATE",
        )

    return None


def validate_cohost_active(
    cohost,
):
    """
    Require an active co-host session.
    """
    if (
        cohost.status != COHOST_STATUS_ACTIVE
        or not bool(cohost.is_active)
    ):
        return fail(
            "Co-host session is not active.",
            error="INVALID_STATE",
        )

    return None


def validate_cohost_not_terminal(
    cohost,
):
    """
    Reject workflows that have reached a terminal state.
    """
    if cohost.status in COHOST_TERMINAL_STATUSES:
        return fail(
            "Co-host workflow has already ended.",
            error="INVALID_STATE",
        )

    return None


def validate_cohost_request_not_expired(
    cohost,
):
    """
    Validate that a pending request or invitation has not expired.

    This validator does not modify the record. The API may mark it expired
    before returning the error.
    """
    if cohost.status != COHOST_STATUS_PENDING:
        return None

    if not cohost.expires_at:
        return None

    if get_datetime(
        cohost.expires_at
    ) <= now_datetime():
        return fail(
            "Co-host request has expired.",
            error="EXPIRED",
        )

    return None


def validate_no_duplicate_cohost_workflow(
    *,
    live_id: str,
    user: str,
    exclude_cohost_id: str | None = None,
):
    """
    Prevent more than one unresolved workflow for the same user and live.

    Unresolved states:
    - pending
    - accepted
    - active
    """
    filters = {
        "live_stream": live_id,
        "user": user,
        "status": [
            "in",
            list(
                COHOST_UNRESOLVED_STATUSES
            ),
        ],
    }

    if exclude_cohost_id:
        filters["name"] = [
            "!=",
            exclude_cohost_id,
        ]

    existing = frappe.db.get_value(
        LIVE_COHOST_DOCTYPE,
        filters,
        [
            "name",
            "request_type",
            "status",
            "expires_at",
        ],
        as_dict=True,
    )

    if existing:
        return existing, fail(
            "This viewer already has an unresolved co-host workflow.",
            error="ALREADY_EXISTS",
        )

    return None, None


def validate_available_cohost_slot(
    *,
    live_id: str,
    exclude_cohost_id: str | None = None,
):
    """
    Validate that the live has room for another accepted or active co-host.

    The initial implementation supports one slot. This uses the configured
    slot constant so the policy can be expanded later.
    """
    filters = {
        "live_stream": live_id,
        "status": [
            "in",
            list(
                COHOST_SLOT_RESERVED_STATUSES
            ),
        ],
    }

    if exclude_cohost_id:
        filters["name"] = [
            "!=",
            exclude_cohost_id,
        ]

    reserved_count = frappe.db.count(
        LIVE_COHOST_DOCTYPE,
        filters=filters,
    )

    if (
        int(reserved_count or 0)
        >= LIVE_COHOST_MAX_ACTIVE_SLOTS
    ):
        return fail(
            "This live stream already has an accepted or active co-host.",
            error="COHOST_SLOT_UNAVAILABLE",
        )

    return None


# CO-HOST PERMISSION VALIDATION
def validate_user_can_respond_to_cohost(
    *,
    cohost,
    live,
    user: str,
):
    """
    Validate who may accept or reject a co-host workflow.

    Host invitation:
    - invited viewer responds.

    Viewer request:
    - live host responds.
    """
    if not is_authenticated_user(user):
        return fail(
            "Login required.",
            error="AUTH_REQUIRED",
        )

    if (
        cohost.request_type
        == COHOST_REQUEST_TYPE_HOST_INVITE
    ):
        if cohost.user != user:
            return fail(
                "Only the invited viewer can respond to this invitation.",
                error="PERMISSION_DENIED",
            )

        return None

    if (
        cohost.request_type
        == COHOST_REQUEST_TYPE_VIEWER_REQUEST
    ):
        if live.host_user != user:
            return fail(
                "Only the live host can respond to this co-host request.",
                error="PERMISSION_DENIED",
            )

        return None

    return fail(
        "Invalid co-host request type.",
        error="INVALID_STATE",
    )


def validate_user_can_cancel_cohost(
    *,
    cohost,
    live,
    user: str,
):
    """
    Validate who may cancel a pending co-host workflow.

    Host invitation:
    - host may cancel their invitation.

    Viewer request:
    - viewer may cancel their request.
    """
    if not is_authenticated_user(user):
        return fail(
            "Login required.",
            error="AUTH_REQUIRED",
        )

    if (
        cohost.request_type
        == COHOST_REQUEST_TYPE_HOST_INVITE
    ):
        if live.host_user != user:
            return fail(
                "Only the live host can cancel this invitation.",
                error="PERMISSION_DENIED",
            )

        return None

    if (
        cohost.request_type
        == COHOST_REQUEST_TYPE_VIEWER_REQUEST
    ):
        if cohost.user != user:
            return fail(
                "Only the requesting viewer can cancel this request.",
                error="PERMISSION_DENIED",
            )

        return None

    return fail(
        "Invalid co-host request type.",
        error="INVALID_STATE",
    )


def validate_user_can_activate_cohost(
    *,
    cohost,
    user: str,
):
    """
    Only the accepted co-host candidate may activate their co-host session.
    """
    if not is_authenticated_user(user):
        return fail(
            "Login required.",
            error="AUTH_REQUIRED",
        )

    if cohost.user != user:
        return fail(
            "Only the accepted co-host can activate this session.",
            error="PERMISSION_DENIED",
        )

    return None


def validate_user_can_end_cohost(
    *,
    cohost,
    live,
    user: str,
):
    """
    Validate who may end an active co-host session.

    Allowed:
    - co-host leaves voluntarily
    - host removes the co-host
    """
    if not is_authenticated_user(user):
        return fail(
            "Login required.",
            error="AUTH_REQUIRED",
        )

    if user == cohost.user:
        return None

    if user == live.host_user:
        return None

    return fail(
        "You are not allowed to end this co-host session.",
        error="PERMISSION_DENIED",
    )


def validate_cohost_session_ownership(
    *,
    cohost,
    user: str,
    session_id: str | None,
):
    """
    Validate that the supplied session is the exact session stored on the
    co-host workflow.
    """
    if cohost.user != user:
        return fail(
            "Co-host workflow does not belong to this user.",
            error="PERMISSION_DENIED",
        )

    session_id = normalize_session_id(
        session_id
    )

    if not session_id:
        return fail(
            "session_id is required.",
            error="VALIDATION_ERROR",
        )

    if cohost.session_id != session_id:
        return fail(
            "Invalid co-host viewer session.",
            error="PERMISSION_DENIED",
        )

    return None
