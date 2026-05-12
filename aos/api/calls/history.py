"""
Call History APIs (implementation).

Handles:
- list_calls
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import LIST_CALLS_LIMIT_PER_MINUTE_PER_USER
from .realtime import serialize_call_for_realtime


# HELPERS
def _clean_int(value, default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default

    if parsed < min_value:
        return min_value

    if parsed > max_value:
        return max_value

    return parsed


def _validate_conversation_membership(
    *,
    conversation_id: str,
    current_user: str,
):
    """
    If caller filters by conversation_id, ensure the user belongs to it.
    """

    conv = frappe.db.get_value(
        "AOS Conversation",
        conversation_id,
        ["participant_1", "participant_2"],
        as_dict=True,
    )

    if not conv:
        return fail("Conversation not found.", code="NOT_FOUND")

    if current_user not in (conv.participant_1, conv.participant_2):
        return fail("Not allowed.", code="PERMISSION_DENIED")

    return None


def _serialize_call_row(call, current_user: str) -> dict:
    """
    Serialize call history row.

    Returns the same rich call shape used by call realtime/API responses,
    plus history-specific direction helpers.
    """

    is_outgoing = call.caller == current_user
    is_incoming = not is_outgoing
    is_missed = call.status == "missed" and is_incoming

    item = serialize_call_for_realtime(
        call,
        current_user=current_user,
    )

    item.update(
        {
            "direction": "outgoing" if is_outgoing else "incoming",
            "is_outgoing": is_outgoing,
            "is_incoming": is_incoming,
            "is_missed": is_missed,
            "created_at": call.creation,
        }
    )

    return item


# LIST CALLS
def list_calls_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:calls:list:user:{current_user}",
        ttl_seconds=60,
        limit=LIST_CALLS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    limit = _clean_int(
        kwargs.get("limit"),
        default=20,
        min_value=1,
        max_value=100,
    )

    offset = _clean_int(
        kwargs.get("offset"),
        default=0,
        min_value=0,
        max_value=100000,
    )

    conversation_id = kwargs.get("conversation_id")
    filter_type = (kwargs.get("type") or "all").strip().lower()

    # Validate filter type
    if filter_type not in ("all", "incoming", "outgoing", "missed"):
        return fail("Invalid type.", code="VALIDATION_ERROR")

    try:
        if conversation_id:
            membership_error = _validate_conversation_membership(
                conversation_id=conversation_id,
                current_user=current_user,
            )
            if membership_error:
                return membership_error

        conditions = [
            "(caller = %(current_user)s OR receiver = %(current_user)s)"
        ]

        values = {
            "current_user": current_user,
            "limit": limit,
            "offset": offset,
        }

        if conversation_id:
            conditions.append("conversation = %(conversation_id)s")
            values["conversation_id"] = conversation_id

        # TYPE FILTERING
        if filter_type == "incoming":
            conditions.append("receiver = %(current_user)s")

        elif filter_type == "outgoing":
            conditions.append("caller = %(current_user)s")

        elif filter_type == "missed":
            conditions.append("receiver = %(current_user)s")
            conditions.append("status = 'missed'")

        where_clause = " AND ".join(conditions)

        # Fetch calls
        calls = frappe.db.sql(
            f"""
            SELECT
                name,
                conversation,
                room_name,
                caller,
                receiver,
                status,
                call_type,
                is_active,
                ringing_at,
                started_at,
                ended_at,
                ended_by,
                duration,
                creation
            FROM `tabAOS Call`
            WHERE {where_clause}
            ORDER BY creation DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            values,
            as_dict=True,
        )

        if not calls:
            return ok("Calls fetched.", data=[])

        results = [
            _serialize_call_row(call, current_user)
            for call in calls
        ]

        return ok("Calls fetched.", data=results)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Calls Failed",
        )
        return fail("Failed to fetch calls.", code="INTERNAL_ERROR")
