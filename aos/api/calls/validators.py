"""Call validators for the canonical multi-participant model."""
from __future__ import annotations

import json

import frappe

from aos.api.shared.responses import fail
from aos.services.accounts.identity import resolve_account_reference
from aos.services.calls.identifiers import internal_call_name
from aos.services.calls.participants import participant_for_user

ACTIVE_CALL_STATUSES = {"initiated", "ringing", "ongoing"}
TERMINAL_CALL_STATUSES = {"ended", "missed", "rejected", "failed", "cancelled"}
MAX_CALL_PARTICIPANTS = 32


def get_call_row(call_id: str):
    call_name = internal_call_name(call_id) if call_id else None
    if not call_name:
        return None
    try:
        return frappe.get_doc("AOS Call", call_name)
    except frappe.DoesNotExistError:
        return None


def validate_call_exists(call_id: str):
    call = get_call_row(call_id)
    if not call:
        return None, fail("Call not found.", error="NOT_FOUND")
    return call, None


def validate_user_in_call(call, user: str):
    if not call or not participant_for_user(call.name, user):
        return fail("Call not found.", error="NOT_FOUND", http_status=404)
    return None


def validate_is_initiator(call, user: str):
    if validate_user_in_call(call, user):
        return fail("Call not found.", error="NOT_FOUND", http_status=404)
    if call.initiator != user:
        return fail("Only the call initiator can perform this action.", error="PERMISSION_DENIED")
    return None


def validate_call_active(call):
    if not call or not int(call.is_active or 0) or call.status in TERMINAL_CALL_STATUSES:
        return fail("Call is no longer active.", error="INVALID_STATE")
    return None


def parse_participant_ids(value) -> tuple[list[str], dict | None]:
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            value = []
        else:
            try:
                value = json.loads(raw)
            except Exception:
                value = [raw]
    if not isinstance(value, (list, tuple, set)):
        return [], fail("participant_ids must be a list of account IDs.", error="VALIDATION_ERROR")
    if len(value) > MAX_CALL_PARTICIPANTS - 1:
        return [], fail("A call supports at most 32 participants including you.", error="VALIDATION_ERROR")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        account_id = str(item or "").strip().upper()
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        result.append(account_id)
    if not result:
        return [], fail("At least one participant is required.", error="VALIDATION_ERROR")
    if len(result) > MAX_CALL_PARTICIPANTS - 1:
        return [], fail("A call supports at most 32 participants including you.", error="VALIDATION_ERROR")
    return result, None


def resolve_participant_users(account_ids: list[str], *, current_user: str) -> tuple[list[str], dict | None]:
    users: list[str] = []
    for account_id in account_ids:
        user = resolve_account_reference(account_id)
        if not user or user == current_user:
            return [], fail("One or more participants are unavailable.", error="ACCOUNT_DISABLED", http_status=404)
        users.append(user)
    if len(set(users)) != len(users):
        return [], fail("Duplicate participants are not allowed.", error="VALIDATION_ERROR")
    return users, None


def validate_direct_conversation(conversation_id: str | None, *, current_user: str, target_user: str):
    """Allow Calls to bind only to the exact active 1:1 Chat conversation."""
    if not conversation_id:
        return None
    conv_type = frappe.db.get_value("AOS Conversation", conversation_id, "conversation_type")
    if conv_type != "direct":
        return fail("Conversation not found.", error="NOT_FOUND", http_status=404)
    users = frappe.get_all(
        "AOS Conversation Participant",
        filters={"conversation": conversation_id, "status": "active"},
        pluck="user",
        limit=3,
    )
    if len(users) != 2 or set(users) != {current_user, target_user}:
        return fail("Conversation not found.", error="NOT_FOUND", http_status=404)
    return None


def validate_conversation_for_call(
    conversation_id: str | None,
    *,
    current_user: str,
    target_users: list[str],
    call_mode: str,
):
    """Bind a call only to the exact active Chat membership set.

    Direct calls require a direct conversation with exactly two active members.
    Group-chat calls require a group conversation whose complete active
    membership matches the call participants. Calls started outside Chat may
    omit conversation_id.
    """
    if not conversation_id:
        return None
    conv_type = frappe.db.get_value("AOS Conversation", conversation_id, "conversation_type")
    if conv_type not in {"direct", "group"} or conv_type != call_mode:
        return fail("Conversation not found.", error="NOT_FOUND", http_status=404)
    users = frappe.get_all(
        "AOS Conversation Participant",
        filters={"conversation": conversation_id, "status": "active"},
        pluck="user",
        limit=MAX_CALL_PARTICIPANTS + 1,
    )
    expected = {current_user, *target_users}
    if len(users) != len(expected) or set(users) != expected:
        return fail("Conversation participants do not match this call.", error="VALIDATION_ERROR", http_status=422)
    if call_mode == "group" and len(users) < 3:
        return fail("A group call requires at least three active group members.", error="VALIDATION_ERROR", http_status=422)
    return None
