"""
Live Stream validators.

Reusable validation helpers for the social Live feature.
"""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail


# FETCH HELPERS
def get_live_row(live_id: str):
    if not live_id:
        return None

    try:
        return frappe.get_doc("AOS Live Stream", live_id)
    except frappe.DoesNotExistError:
        return None


def get_live_view_row(view_id: str):
    if not view_id:
        return None

    try:
        return frappe.get_doc("AOS Live Stream View", view_id)
    except frappe.DoesNotExistError:
        return None


def get_comment_row(comment_id: str):
    if not comment_id:
        return None

    try:
        return frappe.get_doc("AOS Live Stream Comment", comment_id)
    except frappe.DoesNotExistError:
        return None


# LIVE VALIDATION
def validate_live_exists(live_id: str):
    live = get_live_row(live_id)

    if not live:
        return None, fail("Live stream not found.", code="NOT_FOUND")

    return live, None


def validate_live_active(live):
    if live.status != "live" or not live.is_active:
        return fail("Live stream is not active.", code="INVALID_STATE")

    return None


def validate_live_not_ended(live):
    if live.status == "ended":
        return fail("Live stream has ended.", code="INVALID_STATE")

    return None


def validate_user_is_host(live, user: str):
    if not user:
        return fail("Login required.", code="AUTH_REQUIRED")

    if live.host_user != user:
        return fail("Only the host can perform this action.", code="PERMISSION_DENIED")

    return None


def validate_user_can_go_live(user: str):
    """
    Social Live eligibility.

    For now, any enabled logged-in user can go live.
    Keep this helper separate so future rules can be added without touching
    start_live implementation code.
    """
    if not user:
        return None, fail("Login required.", code="AUTH_REQUIRED")

    user_row = frappe.db.get_value(
        "User",
        user,
        ["name", "enabled"],
        as_dict=True,
    )

    if not user_row:
        return None, fail("User not found.", code="NOT_FOUND")

    if not user_row.enabled:
        return None, fail("User account is disabled.", code="INVALID_STATE")

    return user_row, None


# VIEW VALIDATION
def validate_view_identity(user: str | None, session_id: str | None):
    """
    View tracking supports guests.

    session_id is required for both guests and logged-in viewers.
    user is optional and only present for authenticated viewers.
    """
    if not session_id:
        return fail("Session id is required.", code="VALIDATION_ERROR")

    return None


def validate_no_active_view_session(live_id: str, user: str | None, session_id: str | None):
    if not session_id:
        return fail("Session id is required.", code="VALIDATION_ERROR")

    exists = frappe.db.exists(
        "AOS Live Stream View",
        {
            "live_stream": live_id,
            "session_id": session_id,
            "is_active": 1,
        },
    )

    if exists:
        return fail("Active view session already exists.", code="INVALID_STATE")

    return None


def validate_active_view_session(live_id: str, user: str | None, session_id: str | None):
    if not session_id:
        return None, fail("Session id is required.", code="VALIDATION_ERROR")

    filters = {
        "live_stream": live_id,
        "session_id": session_id,
        "is_active": 1,
    }

    view = frappe.db.get_value(
        "AOS Live Stream View",
        filters,
        ["name"],
        as_dict=True,
    )

    if not view:
        return None, fail("Active session not found.", code="NOT_FOUND")

    return view, None


# COMMENT VALIDATION
def validate_comment_exists(comment_id: str):
    comment = get_comment_row(comment_id)

    if not comment:
        return None, fail("Comment not found.", code="NOT_FOUND")

    return comment, None


def validate_comment_belongs_to_live(comment, live_id: str):
    if comment.live_stream != live_id:
        return fail("Comment does not belong to this live stream.", code="INVALID_STATE")

    return None


def validate_comment_active(comment):
    if comment.status != "active":
        return fail("Comment is not active.", code="INVALID_STATE")

    return None


def validate_user_can_delete_comment(comment, user: str):
    if not user:
        return fail("Login required.", code="AUTH_REQUIRED")

    if comment.user == user:
        return None

    live = get_live_row(comment.live_stream)

    if live and live.host_user == user:
        return None

    return fail("You cannot delete this comment.", code="PERMISSION_DENIED")
