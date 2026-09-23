"""
Call History APIs (implementation).

Handles:
- list_calls
- get_call_group_details
- delete_call_logs
- clear_call_history
"""

from __future__ import annotations
from typing import Any

import json
import frappe
from frappe.utils import get_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok, fail
from aos.api.shared.user_display import get_user_display_map

from .constants import (
    LIST_CALLS_LIMIT_PER_MINUTE_PER_USER,
    GET_CALL_GROUP_DETAILS_LIMIT_PER_MINUTE_PER_USER,
    DELETE_CALL_LOGS_LIMIT_PER_MINUTE_PER_USER,
    CLEAR_CALL_HISTORY_LIMIT_PER_MINUTE_PER_USER,
)
from .realtime import serialize_call_for_realtime


# CONSTANTS
RAW_FETCH_BATCH_SIZE = 100
MAX_GROUP_FETCH_LOOPS = 50
MAX_DELETE_CALL_LOGS_BATCH_SIZE = 100
CLEAR_CALL_HISTORY_BATCH_SIZE = 500
MAX_GROUP_DETAIL_ROWS = 5000


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


def _clean_str(value) -> str | None:
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def _normalize_call_ids(value) -> list[str]:
    """
    Normalize call_ids from JSON/list/string input.

    Accepts:
    - ["call_<opaque>", "call_<opaque>"]
    - '["call_<opaque>", "call_<opaque>"]'
    - "call_<opaque>"
    """

    if value is None:
        return []

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return []

        try:
            parsed = json.loads(value)
            value = parsed
        except Exception:
            value = [value]

    if not isinstance(value, (list, tuple, set)):
        value = [value]

    result = []
    seen = set()

    for item in value:
        call_id = _clean_str(item)

        if not call_id:
            continue

        if call_id in seen:
            continue

        seen.add(call_id)
        result.append(call_id)

    return result


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
        return fail("Conversation not found.", error="NOT_FOUND")

    if current_user not in (conv.participant_1, conv.participant_2):
        return fail("Conversation not found.", error="NOT_FOUND")

    return None


def _visible_call_condition() -> str:
    """
    Per-user call-log visibility condition.

    Mirrors AOS Conversation soft-delete behavior:
    - caller deletion hides only from caller
    - receiver deletion hides only from receiver
    """

    return """
    (
        (
            caller = %(current_user)s
            AND IFNULL(visible_to_caller, 1) = 1
        )
        OR
        (
            receiver = %(current_user)s
            AND IFNULL(visible_to_receiver, 1) = 1
        )
    )
    """


def _get_call_row(call_id: str):
    return frappe.db.sql(
        """
        SELECT
            name,
            public_id,
            conversation,
            caller,
            receiver,
            status,
            call_type,
            is_active,
            state_version,
            rtc_provisioned_at,
            incoming_dispatched_at,
            ring_expires_at,
            visible_to_caller,
            visible_to_receiver,
            ringing_at,
            started_at,
            ended_at,
            ended_by,
            duration,
            video_upgrade_status,
            video_upgrade_requested_by,
            video_upgrade_requested_at,
            video_upgrade_responded_at,
            creation
        FROM `tabAOS Call`
        WHERE public_id = %(call_id)s
        LIMIT 1
        """,
        {"call_id": call_id},
        as_dict=True,
    )


def _fetch_call_by_id(call_id: str):
    rows = _get_call_row(call_id)
    return rows[0] if rows else None


def _user_in_call(call, current_user: str) -> bool:
    return current_user in (call.caller, call.receiver)


def _user_can_see_call(call, current_user: str) -> bool:
    if call.caller == current_user:
        return int(call.visible_to_caller or 0) == 1

    if call.receiver == current_user:
        return int(call.visible_to_receiver or 0) == 1

    return False


def _get_direction(call, current_user: str) -> str:
    return "outgoing" if call.caller == current_user else "incoming"


def _get_other_user(call, current_user: str) -> str | None:
    if call.caller == current_user:
        return call.receiver

    if call.receiver == current_user:
        return call.caller

    return None


def _get_history_category(call, current_user: str) -> str:
    """
    Convert raw call status into a user-facing history category.

    Important:
    - Receiver sees cancelled incoming calls as missed.
    - Caller sees cancelled calls as cancelled.
    - Caller sees missed timeout as not_answered.
    """

    direction = _get_direction(call, current_user)
    status = call.status

    if direction == "incoming":
        if status in ("missed", "cancelled"):
            return "missed"

        if status == "rejected":
            return "declined"

        if status == "ended":
            return "completed"

        if status in ("initiated", "ringing", "ongoing"):
            return "active"

        if status == "failed":
            return "failed"

        return status or "unknown"

    # outgoing
    if status == "missed":
        return "not_answered"

    if status == "cancelled":
        return "cancelled"

    if status == "rejected":
        return "declined"

    if status == "ended":
        return "completed"

    if status in ("initiated", "ringing", "ongoing"):
        return "active"

    if status == "failed":
        return "failed"

    return status or "unknown"


def _serialize_call_row(
    call,
    current_user: str,
    *,
    user_summaries: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """
    Serialize one raw call row.

    Returns the same rich call shape used by call realtime/API responses,
    plus history-specific direction/category helpers.
    """

    direction = _get_direction(call, current_user)
    is_outgoing = direction == "outgoing"
    is_incoming = direction == "incoming"
    history_category = _get_history_category(call, current_user)

    is_missed = is_incoming and history_category == "missed"

    item = serialize_call_for_realtime(
        call,
        current_user=current_user,
        user_summaries=user_summaries,
    )

    item.update(
        {
            "direction": direction,
            "history_category": history_category,
            "is_outgoing": is_outgoing,
            "is_incoming": is_incoming,
            "is_missed": is_missed,
            "created_at": call.creation,
        }
    )

    return item


def _display_map_for_calls(calls) -> dict[str, dict[str, Any]]:
    users: set[str] = set()
    for call in calls or ():
        for field in ("caller", "receiver", "ended_by", "video_upgrade_requested_by"):
            value = getattr(call, field, None)
            if value:
                users.add(value)
    return get_user_display_map(users) if users else {}


def _group_compare_key(serialized_call: dict) -> tuple:
    """
    Consecutive calls belong to the same group only if this key matches.

    No time-window grouping is used.
    """

    return (
        serialized_call.get("other_user"),
        serialized_call.get("direction"),
        serialized_call.get("call_type"),
        serialized_call.get("history_category"),
    )


def _new_group(serialized_call: dict) -> dict[str, Any]:
    key = _group_compare_key(serialized_call)

    return {
        "_compare_key": key,
        "group_count": 1,
        "direction": serialized_call.get("direction"),
        "history_category": serialized_call.get("history_category"),
        "call_type": serialized_call.get("call_type"),
        "other_user": serialized_call.get("other_user"),
        "other_display_name": serialized_call.get("other_display_name"),
        "other_avatar": serialized_call.get("other_avatar"),
        "latest_call_id": serialized_call.get("call_id"),
        "oldest_call_id": serialized_call.get("call_id"),
        "created_at": serialized_call.get("created_at"),
        "latest_call": serialized_call,
        "_oldest_call": serialized_call,
    }


def _add_to_group(group: dict, serialized_call: dict):
    group["group_count"] += 1
    group["oldest_call_id"] = serialized_call.get("call_id")
    group["_oldest_call"] = serialized_call


def _finalize_group(group: dict) -> dict:
    group.pop("_compare_key", None)
    oldest_call = group.pop("_oldest_call", None)

    group["group_key"] = (
        f"{group.get('direction')}:"
        f"{group.get('history_category')}:"
        f"{group.get('call_type')}:"
        f"{group.get('other_user')}:"
        f"{group.get('latest_call_id')}:"
        f"{group.get('oldest_call_id')}"
    )

    group["cursor"] = {
        "cursor_created_at": oldest_call.get("created_at") if oldest_call else None,
        "cursor_call_id": oldest_call.get("call_id") if oldest_call else None,
    }

    return group


def _build_base_conditions_and_values(
    *,
    current_user: str,
    conversation_id: str | None,
    filter_type: str,
):
    conditions = [
        _visible_call_condition(),
    ]

    values = {
        "current_user": current_user,
    }

    if conversation_id:
        conditions.append("conversation = %(conversation_id)s")
        values["conversation_id"] = conversation_id

    # TYPE FILTERING
    if filter_type == "incoming":
        # Incoming tab should show received calls that were actively handled.
        # Missed/cancelled unanswered incoming calls belong in the missed tab.
        conditions.append("receiver = %(current_user)s")
        conditions.append("status NOT IN ('missed', 'cancelled')")

    elif filter_type == "outgoing":
        conditions.append("caller = %(current_user)s")

    elif filter_type == "missed":
        # From the receiver's perspective, both missed and caller-cancelled
        # calls are unanswered incoming call attempts.
        conditions.append("receiver = %(current_user)s")
        conditions.append("status IN ('missed', 'cancelled')")

    return conditions, values


def _fetch_call_rows(
    *,
    conditions: list[str],
    values: dict,
    cursor_created_at=None,
    cursor_name: str | None = None,
    limit: int = RAW_FETCH_BATCH_SIZE,
):
    local_conditions = list(conditions)
    local_values = dict(values)

    if cursor_created_at and cursor_name:
        local_conditions.append(
            """
            (
                creation < %(cursor_created_at)s
                OR (
                    creation = %(cursor_created_at)s
                    AND name < %(cursor_name)s
                )
            )
            """
        )
        local_values["cursor_created_at"] = cursor_created_at
        local_values["cursor_name"] = cursor_name

    local_values["limit"] = limit

    where_clause = " AND ".join(local_conditions)

    return frappe.db.sql(
        f"""
        SELECT
            name,
            public_id,
            conversation,
            caller,
            receiver,
            status,
            call_type,
            is_active,
            state_version,
            rtc_provisioned_at,
            incoming_dispatched_at,
            ring_expires_at,
            visible_to_caller,
            visible_to_receiver,
            ringing_at,
            started_at,
            ended_at,
            ended_by,
            duration,
            video_upgrade_status,
            video_upgrade_requested_by,
            video_upgrade_requested_at,
            video_upgrade_responded_at,
            creation
        FROM `tabAOS Call`
        WHERE {where_clause}
        ORDER BY creation DESC, name DESC
        LIMIT %(limit)s
        """,
        local_values,
        as_dict=True,
    )


def _build_grouped_history(
    *,
    current_user: str,
    conditions: list[str],
    values: dict,
    limit: int,
    cursor_created_at=None,
    cursor_name: str | None = None,
):
    """
    Build grouped call-history rows.

    Production rule:
    - Consecutive similar rows are grouped.
    - Groups are not split across pages.
    - next_cursor points to the oldest raw call inside the last returned group.
    """

    groups: list[dict] = []
    current_group = None

    batch_cursor_created_at = cursor_created_at
    batch_cursor_name = cursor_name

    reached_page_limit = False
    exhausted = False

    for _ in range(MAX_GROUP_FETCH_LOOPS):
        rows = _fetch_call_rows(
            conditions=conditions,
            values=values,
            cursor_created_at=batch_cursor_created_at,
            cursor_name=batch_cursor_name,
            limit=RAW_FETCH_BATCH_SIZE,
        )

        if not rows:
            exhausted = True
            break

        user_summaries = _display_map_for_calls(rows)

        for call in rows:
            serialized = _serialize_call_row(
                call,
                current_user,
                user_summaries=user_summaries,
            )
            key = _group_compare_key(serialized)

            if current_group is None:
                current_group = _new_group(serialized)

            elif key == current_group.get("_compare_key"):
                _add_to_group(current_group, serialized)

            else:
                finalized = _finalize_group(current_group)
                groups.append(finalized)

                # We have completed the Nth group only because we saw the
                # first row of the next group. That means the Nth group is not
                # split across pages.
                if len(groups) >= limit:
                    reached_page_limit = True
                    break

                current_group = _new_group(serialized)

        if reached_page_limit:
            break

        last_row = rows[-1]
        batch_cursor_created_at = last_row.creation
        batch_cursor_name = last_row.name

        if len(rows) < RAW_FETCH_BATCH_SIZE:
            exhausted = True
            break

    if not reached_page_limit and current_group is not None:
        groups.append(_finalize_group(current_group))

    groups = groups[:limit]

    next_cursor = None
    if groups and not exhausted:
        next_cursor = groups[-1].get("cursor")

    return {
        "items": groups,
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor),
    }


def _validate_group_boundary_call(
    *,
    call_id: str,
    current_user: str,
    label: str,
):
    call = _fetch_call_by_id(call_id)

    if not call:
        return None, fail(f"{label} call not found.", error="NOT_FOUND")

    if not _user_in_call(call, current_user):
        # Avoid exposing whether a valid Call ID belongs to another account.
        return None, fail(f"{label} call not found.", error="NOT_FOUND")

    if not _user_can_see_call(call, current_user):
        return None, fail(f"{label} call not found.", error="NOT_FOUND")

    return call, None


def _fetch_calls_between_boundaries(
    *,
    current_user: str,
    latest_call,
    oldest_call,
):
    """
    Fetch visible calls between latest and oldest boundary calls, inclusive.

    Ordering is DESC by creation/name.
    """

    return frappe.db.sql(
        f"""
        SELECT
            name,
            public_id,
            conversation,
            caller,
            receiver,
            status,
            call_type,
            is_active,
            state_version,
            rtc_provisioned_at,
            incoming_dispatched_at,
            ring_expires_at,
            visible_to_caller,
            visible_to_receiver,
            ringing_at,
            started_at,
            ended_at,
            ended_by,
            duration,
            video_upgrade_status,
            video_upgrade_requested_by,
            video_upgrade_requested_at,
            video_upgrade_responded_at,
            creation
        FROM `tabAOS Call`
        WHERE
            {_visible_call_condition()}
            AND (
                creation < %(latest_creation)s
                OR (
                    creation = %(latest_creation)s
                    AND name <= %(latest_name)s
                )
            )
            AND (
                creation > %(oldest_creation)s
                OR (
                    creation = %(oldest_creation)s
                    AND name >= %(oldest_name)s
                )
            )
        ORDER BY creation DESC, name DESC
        LIMIT %(max_rows)s
        """,
        {
            "current_user": current_user,
            "latest_creation": latest_call.creation,
            "latest_name": latest_call.name,
            "oldest_creation": oldest_call.creation,
            "oldest_name": oldest_call.name,
            "max_rows": MAX_GROUP_DETAIL_ROWS + 1,
        },
        as_dict=True,
    )


def _delete_selected_call_logs(*, current_user: str, call_ids: list[str]) -> int:
    """
    Hide selected call logs for current user only.

    Does not delete AOS Call records.
    Does not affect the other participant.
    """

    if not call_ids:
        return 0

    deleted_count = 0

    placeholders = ", ".join(["%s"] * len(call_ids))

    # Hide logs where current user is caller.
    caller_query = f"""
        UPDATE `tabAOS Call`
        SET visible_to_caller = 0
        WHERE caller = %s
          AND IFNULL(visible_to_caller, 1) = 1
          AND public_id IN ({placeholders})
    """

    frappe.db.sql(
        caller_query,
        tuple([current_user] + call_ids),
    )
    deleted_count += frappe.db._cursor.rowcount or 0

    # Hide logs where current user is receiver.
    receiver_query = f"""
        UPDATE `tabAOS Call`
        SET visible_to_receiver = 0
        WHERE receiver = %s
          AND IFNULL(visible_to_receiver, 1) = 1
          AND public_id IN ({placeholders})
    """

    frappe.db.sql(
        receiver_query,
        tuple([current_user] + call_ids),
    )
    deleted_count += frappe.db._cursor.rowcount or 0

    return deleted_count


def _clear_all_call_history(*, current_user: str) -> int:
    """Hide visible history in bounded, lock-safe batches for this user only."""

    deleted_count = 0

    for role_field, visible_field in (
        ("caller", "visible_to_caller"),
        ("receiver", "visible_to_receiver"),
    ):
        while True:
            rows = frappe.db.sql(
                f"""
                SELECT name
                FROM `tabAOS Call`
                WHERE {role_field} = %(current_user)s
                  AND IFNULL({visible_field}, 1) = 1
                ORDER BY creation ASC, name ASC
                LIMIT %(limit)s
                FOR UPDATE
                """,
                {
                    "current_user": current_user,
                    "limit": CLEAR_CALL_HISTORY_BATCH_SIZE,
                },
                as_dict=True,
            )
            if not rows:
                break

            names = tuple(row.name for row in rows)
            frappe.db.sql(
                f"""
                UPDATE `tabAOS Call`
                SET {visible_field} = 0
                WHERE {role_field} = %(current_user)s
                  AND IFNULL({visible_field}, 1) = 1
                  AND name IN %(names)s
                """,
                {"current_user": current_user, "names": names},
            )
            deleted_count += frappe.db._cursor.rowcount or 0

    return deleted_count


def _resolve_history_cursor(*, current_user: str, cursor_call_id: str, cursor_created_at: str):
    call, err = _validate_group_boundary_call(
        call_id=cursor_call_id,
        current_user=current_user,
        label="Cursor",
    )
    if err:
        return None, None, err

    # Parse the timestamp so malformed cursors fail predictably, but never trust
    # a client-supplied sort key for pagination. The authorized opaque call ID
    # resolves the canonical database creation/name boundary server-side.
    try:
        get_datetime(cursor_created_at)
    except Exception:
        return None, None, fail("Invalid call-history cursor.", error="VALIDATION_ERROR")

    return call.creation, call.name, None


# LIST CALLS
def list_calls_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("calls", "list", current_user),
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

    conversation_id = _clean_str(kwargs.get("conversation_id"))
    filter_type = (kwargs.get("type") or "all").strip().lower()

    cursor_created_at = _clean_str(kwargs.get("cursor_created_at"))
    cursor_call_id = _clean_str(kwargs.get("cursor_call_id"))

    # Validate filter type
    if filter_type not in ("all", "incoming", "outgoing", "missed"):
        return fail("Invalid type.", error="VALIDATION_ERROR")

    if bool(cursor_created_at) != bool(cursor_call_id):
        return fail(
            "cursor_created_at and cursor_call_id must be provided together.",
            error="VALIDATION_ERROR",
        )

    try:
        if conversation_id:
            membership_error = _validate_conversation_membership(
                conversation_id=conversation_id,
                current_user=current_user,
            )
            if membership_error:
                return membership_error

        conditions, values = _build_base_conditions_and_values(
            current_user=current_user,
            conversation_id=conversation_id,
            filter_type=filter_type,
        )

        cursor_name = None
        if cursor_call_id:
            cursor_created_at, cursor_name, cursor_error = _resolve_history_cursor(
                current_user=current_user,
                cursor_call_id=cursor_call_id,
                cursor_created_at=cursor_created_at,
            )
            if cursor_error:
                return cursor_error

        result = _build_grouped_history(
            current_user=current_user,
            conditions=conditions,
            values=values,
            limit=limit,
            cursor_created_at=cursor_created_at,
            cursor_name=cursor_name,
        )

        return ok("Calls fetched.", data=result)

    except Exception:
        frappe.log_error("Unexpected Calls history failure.", "AOS List Calls Failed")
        return fail("Failed to fetch calls.", error="INTERNAL_ERROR")


# GET CALL GROUP DETAILS
def get_call_group_details_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("calls", "group_details", current_user),
        ttl_seconds=60,
        limit=GET_CALL_GROUP_DETAILS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    latest_call_id = _clean_str(kwargs.get("latest_call_id"))
    oldest_call_id = _clean_str(kwargs.get("oldest_call_id"))

    if not latest_call_id:
        return fail("latest_call_id is required.", error="VALIDATION_ERROR")

    if not oldest_call_id:
        return fail("oldest_call_id is required.", error="VALIDATION_ERROR")

    try:
        latest_call, err = _validate_group_boundary_call(
            call_id=latest_call_id,
            current_user=current_user,
            label="Latest",
        )
        if err:
            return err

        oldest_call, err = _validate_group_boundary_call(
            call_id=oldest_call_id,
            current_user=current_user,
            label="Oldest",
        )
        if err:
            return err

        boundary_summaries = _display_map_for_calls((latest_call, oldest_call))
        latest_serialized = _serialize_call_row(
            latest_call, current_user, user_summaries=boundary_summaries
        )
        oldest_serialized = _serialize_call_row(
            oldest_call, current_user, user_summaries=boundary_summaries
        )

        expected_key = _group_compare_key(latest_serialized)

        if expected_key != _group_compare_key(oldest_serialized):
            return fail("Invalid call group.", error="VALIDATION_ERROR")

        rows = _fetch_calls_between_boundaries(
            current_user=current_user,
            latest_call=latest_call,
            oldest_call=oldest_call,
        )

        if len(rows) > MAX_GROUP_DETAIL_ROWS:
            return fail(
                "Call group is too large to return in one response.",
                error="CALL_INPUT_TOO_LARGE",
            )

        calls = []
        row_summaries = _display_map_for_calls(rows)

        for row in rows:
            serialized = _serialize_call_row(
                row, current_user, user_summaries=row_summaries
            )

            if _group_compare_key(serialized) != expected_key:
                break

            calls.append(serialized)

            if row.name == oldest_call.name:
                break

        if not calls:
            return fail("Invalid call group.", error="VALIDATION_ERROR")

        if calls[0].get("call_id") != latest_call_id:
            return fail("Invalid call group boundary.", error="VALIDATION_ERROR")

        if calls[-1].get("call_id") != oldest_call_id:
            return fail("Invalid call group boundary.", error="VALIDATION_ERROR")

        latest = calls[0]

        data = {
            "group_key": (
                f"{latest.get('direction')}:"
                f"{latest.get('history_category')}:"
                f"{latest.get('call_type')}:"
                f"{latest.get('other_user')}:"
                f"{latest_call_id}:"
                f"{oldest_call_id}"
            ),
            "group_count": len(calls),
            "direction": latest.get("direction"),
            "history_category": latest.get("history_category"),
            "call_type": latest.get("call_type"),
            "other_user": latest.get("other_user"),
            "other_display_name": latest.get("other_display_name"),
            "other_avatar": latest.get("other_avatar"),
            "latest_call_id": latest_call_id,
            "oldest_call_id": oldest_call_id,
            "created_at": latest.get("created_at"),
            "calls": calls,
        }

        return ok("Call group fetched.", data=data)

    except Exception:
        frappe.log_error(
            "Unexpected Calls group-details failure.",
            "AOS Get Call Group Details Failed",
        )
        return fail("Failed to fetch call group.", error="INTERNAL_ERROR")


# DELETE CALL LOGS
def delete_call_logs_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("calls", "delete_logs", current_user),
        ttl_seconds=60,
        limit=DELETE_CALL_LOGS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    call_ids = _normalize_call_ids(kwargs.get("call_ids"))

    if not call_ids:
        return fail("call_ids is required.", error="VALIDATION_ERROR")

    if len(call_ids) > MAX_DELETE_CALL_LOGS_BATCH_SIZE:
        return fail(
            f"You can delete at most {MAX_DELETE_CALL_LOGS_BATCH_SIZE} call logs at once.",
            error="VALIDATION_ERROR",
        )

    try:
        deleted_count = _delete_selected_call_logs(
            current_user=current_user,
            call_ids=call_ids,
        )

        return ok(
            "Call logs deleted.",
            data={
                "deleted_count": deleted_count,
            },
        )

    except Exception:
        frappe.log_error(
            "Unexpected Calls history deletion failure.",
            "AOS Delete Call Logs Failed",
        )
        return fail("Failed to delete call logs.", error="INTERNAL_ERROR")


# CLEAR CALL HISTORY
def clear_call_history_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("calls", "clear_history", current_user),
        ttl_seconds=60,
        limit=CLEAR_CALL_HISTORY_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        deleted_count = _clear_all_call_history(
            current_user=current_user,
        )

        return ok(
            "Call history cleared.",
            data={
                "deleted_count": deleted_count,
            },
        )

    except Exception:
        frappe.log_error(
            "Unexpected Calls history clear failure.",
            "AOS Clear Call History Failed",
        )
        return fail("Failed to clear call history.", error="INTERNAL_ERROR")
