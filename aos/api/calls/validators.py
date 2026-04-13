"""
Call validators.

Reusable validation helpers for call feature.
"""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail


# FETCH HELPERS
def get_call_row(call_id: str):
    try:
        return frappe.get_doc("AOS Call", call_id)
    except frappe.DoesNotExistError:
        return None


def get_conversation_row(conv_id: str):
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
        return None, fail("Call not found.", code="NOT_FOUND")

    return call, None


def validate_conversation_exists(conv_id: str):
    conv = get_conversation_row(conv_id)

    if not conv:
        return None, fail("Conversation not found.", code="NOT_FOUND")

    return conv, None


# USER VALIDATION
def validate_user_in_call(call, user: str):
    if user not in (call.caller, call.receiver):
        return fail("Not allowed.", code="PERMISSION_DENIED")

    return None


def validate_user_in_conversation(conv, user: str):
    if user not in (conv.participant_1, conv.participant_2):
        return fail("Not allowed.", code="PERMISSION_DENIED")

    return None


def get_other_user(call, current_user: str) -> str:
    return (
        call.receiver
        if call.caller == current_user
        else call.caller
    )


# ROLE VALIDATION
def validate_is_caller(call, user: str):
    if call.caller != user:
        return fail("Only caller can perform this action.", code="PERMISSION_DENIED")

    return None


def validate_is_receiver(call, user: str):
    if call.receiver != user:
        return fail("Only receiver can perform this action.", code="PERMISSION_DENIED")

    return None


# STATE VALIDATION
def validate_call_active(call):
    if not call.is_active:
        return fail("Call is no longer active.", code="INVALID_STATE")

    return None


def validate_can_mark_ringing(call):
    if call.status not in ("initiated", "ringing"):
        return fail("Call cannot be marked as ringing.", code="INVALID_STATE")

    return None


def validate_can_accept(call):
    if call.status != "ringing":
        return fail("Call cannot be accepted.", code="INVALID_STATE")

    return None


def validate_can_reject(call):
    if call.status != "ringing":
        return fail("Call cannot be rejected.", code="INVALID_STATE")

    return None


def validate_can_cancel(call):
    if call.status not in ("initiated", "ringing"):
        return fail("Call cannot be cancelled.", code="INVALID_STATE")

    return None


def validate_can_end(call):
    if call.status != "ongoing":
        return fail("Call cannot be ended.", code="INVALID_STATE")

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
            code="ACTIVE_CALL_EXISTS",
        )

    return None
