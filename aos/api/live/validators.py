"""
Live Stream validators.

Reusable validation helpers for the social Live feature.

Responsibilities:
- Live stream validation
- Host and go-live eligibility validation
- Viewer-session validation
"""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail


LIVE_STREAM_DOCTYPE = "AOS Live Stream"
LIVE_VIEW_DOCTYPE = "AOS Live Stream View"
USER_DOCTYPE = "User"

LIVE_STATUS = "live"
ENDED_STATUS = "ended"


# FETCH HELPERS
def get_live_row(live_id: str):
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


def get_live_view_row(view_id: str):
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


# LIVE VALIDATION
def validate_live_exists(live_id: str):
    """
    Validate that a live stream exists.

    Returns:
        tuple[live | None, error_response | None]
    """
    live = get_live_row(live_id)

    if not live:
        return None, fail(
            "Live stream not found.",
            code="NOT_FOUND",
        )

    return live, None


def validate_live_active(live):
    """
    Validate that a live stream is currently active.
    """
    if (
        live.status != LIVE_STATUS
        or not bool(live.is_active)
    ):
        return fail(
            "Live stream is not active.",
            code="INVALID_STATE",
        )

    return None


def validate_live_not_ended(live):
    """
    Validate that the live stream has not ended.
    """
    if live.status == ENDED_STATUS:
        return fail(
            "Live stream has ended.",
            code="INVALID_STATE",
        )

    return None


def validate_user_is_host(
    live,
    user: str,
):
    """
    Validate that the supplied user owns the live stream.
    """

    if not user or user == "Guest":
        return fail(
            "Login required.",
            code="AUTH_REQUIRED",
        )

    if live.host_user != user:
        return fail(
            "Only the host can perform this action.",
            code="PERMISSION_DENIED",
        )

    return None


def validate_user_can_go_live(user: str):
    """
    Validate whether a user can start a live stream.

    Current policy:
    - User must be logged in.
    - User must exist.
    - User account must be enabled.

    Keep this validation separate so future eligibility rules can be added,
    such as:
    - minimum account age
    - suspension checks
    - live-stream restrictions
    - verification or follower requirements
    """

    if not user or user == "Guest":
        return None, fail(
            "Login required.",
            code="AUTH_REQUIRED",
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
            code="NOT_FOUND",
        )

    if not bool(user_row.enabled):
        return None, fail(
            "User account is disabled.",
            code="INVALID_STATE",
        )

    return user_row, None


# VIEW VALIDATION
def validate_view_identity(
    user: str | None,
    session_id: str | None,
):
    """
    Validate a viewer-tracking identity.

    View tracking supports:
    - logged-in viewers
    - guest viewers

    session_id is required for both. The optional user field associates the
    session with an authenticated AOS user.
    """
    if not session_id:
        return fail(
            "Session id is required.",
            code="VALIDATION_ERROR",
        )

    if user and user != "Guest":
        enabled = frappe.db.get_value(
            USER_DOCTYPE,
            user,
            "enabled",
        )

        if enabled is None:
            return fail(
                "Viewer user not found.",
                code="NOT_FOUND",
            )

        if not bool(enabled):
            return fail(
                "Viewer account is disabled.",
                code="INVALID_STATE",
            )

    return None


def validate_no_active_view_session(
    live_id: str,
    user: str | None,
    session_id: str | None,
):
    """
    Ensure the same session does not create multiple active view rows.

    session_id is the canonical viewer-session identifier. This works for
    both guests and authenticated viewers.
    """
    if not session_id:
        return fail(
            "Session id is required.",
            code="VALIDATION_ERROR",
        )

    exists = frappe.db.exists(
        LIVE_VIEW_DOCTYPE,
        {
            "live_stream": live_id,
            "session_id": session_id,
            "is_active": 1,
        },
    )

    if exists:
        return fail(
            "Active view session already exists.",
            code="INVALID_STATE",
        )

    return None


def validate_active_view_session(
    live_id: str,
    user: str | None,
    session_id: str | None,
):
    """
    Fetch and validate an active view session.

    The user argument is used as an additional ownership check for
    authenticated viewers. Guest sessions are identified using session_id.
    """
    if not session_id:
        return None, fail(
            "Session id is required.",
            code="VALIDATION_ERROR",
        )

    filters = {
        "live_stream": live_id,
        "session_id": session_id,
        "is_active": 1,
    }

    if user and user != "Guest":
        filters["user"] = user

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
        ],
        as_dict=True,
    )

    if not view:
        return None, fail(
            "Active view session not found.",
            code="NOT_FOUND",
        )

    return view, None
