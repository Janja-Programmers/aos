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
    if not conversation_id:
        return None
    conv = frappe.db.get_value(
        "AOS Conversation",
        conversation_id,
        ["participant_1", "participant_2"],
        as_dict=True,
    )
    if not conv or {conv.participant_1, conv.participant_2} != {current_user, target_user}:
        return fail("Conversation not found.", error="NOT_FOUND", http_status=404)
    return None
