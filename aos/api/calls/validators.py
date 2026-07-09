"""
Call validators.

Reusable validation helpers for call feature.
"""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail


# Constants
ACTIVE_CALL_STATUSES = {"initiated", "ringing", "ongoing"}
TERMINAL_CALL_STATUSES = {"ended", "missed", "rejected", "failed", "cancelled"}


# FETCH HELPERS
def get_call_row(call_id: str):
    if not call_id:
        return None

    try:
        return frappe.get_doc("AOS Call", call_id)
    except frappe.DoesNotExistError:
        return None


def get_conversation_row(conv_id: str):
    if not conv_id:
        return None

    return frappe.db.get_value(
        "AOS Conversation",
        conv_id,
        ["participant_1", "participant_2"],
        as_dict=True,
    )


# BASIC VALIDATION
def validate_call_exists(call_id: str):
    call = get_call_row(call_id)

    if not call:
        return None, fail("Call not found.", error="NOT_FOUND")

    return call, None


def validate_conversation_exists(conv_id: str):
    conv = get_conversation_row(conv_id)

    if not conv:
        return None, fail("Conversation not found.", error="NOT_FOUND")

    return conv, None


# USER VALIDATION
def validate_user_in_call(call, user: str):
    if not call or user not in (call.caller, call.receiver):
        return fail("Not allowed.", error="PERMISSION_DENIED")

    return None


def validate_user_in_conversation(conv, user: str):
    if not conv or user not in (conv.participant_1, conv.participant_2):
        return fail("Not allowed.", error="PERMISSION_DENIED")

    return None


def get_other_user(call, current_user: str) -> str | None:
    if not call:
        return None

    if call.caller == current_user:
        return call.receiver

    if call.receiver == current_user:
        return call.caller

    return None


# ROLE VALIDATION
def validate_is_caller(call, user: str):
    if not call or call.caller != user:
        return fail(
            "Only caller can perform this action.",
            error="PERMISSION_DENIED",
        )

    return None


def validate_is_receiver(call, user: str):
    if not call or call.receiver != user:
        return fail(
            "Only receiver can perform this action.",
            error="PERMISSION_DENIED",
        )

    return None


# STATE VALIDATION
def validate_call_active(call):
    if not call or not call.is_active:
        return fail("Call is no longer active.", error="INVALID_STATE")

    if call.status in TERMINAL_CALL_STATUSES:
        return fail("Call is no longer active.", error="INVALID_STATE")

    return None


def validate_can_mark_ringing(call):
    if not call or call.status not in ("initiated", "ringing"):
        return fail("Call cannot be marked as ringing.", error="INVALID_STATE")

    return None


def validate_can_accept(call):
    if not call or call.status not in ("initiated", "ringing"):
        return fail("Call cannot be accepted.", error="INVALID_STATE")

    return None


def validate_can_reject(call):
    if not call or call.status not in ("initiated", "ringing"):
        return fail("Call cannot be rejected.", error="INVALID_STATE")

    return None


def validate_can_cancel(call):
    if not call or call.status not in ("initiated", "ringing"):
        return fail("Call cannot be cancelled.", error="INVALID_STATE")

    return None


def validate_can_end(call):
    if not call or call.status != "ongoing":
        return fail("Call cannot be ended.", error="INVALID_STATE")

    return None


# ACTIVE CALL CONSTRAINT
def validate_no_active_call_for_conversation(conv_id: str):
    exists = frappe.db.exists(
        "AOS Call",
        {
            "conversation": conv_id,
            "is_active": 1,
        },
    )

    if exists:
        return fail(
            "There is already an active call for this conversation.",
            error="ACTIVE_CALL_EXISTS",
        )

    return None
