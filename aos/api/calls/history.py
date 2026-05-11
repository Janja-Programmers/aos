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


# HELPERS
def _serialize_call_row(call, current_user: str):
    """
    Shape response + compute direction + missed state.
    """
    is_caller = call.caller == current_user

    direction = "outgoing" if is_caller else "incoming"
    other_user = call.receiver if is_caller else call.caller
    is_missed = (call.status == "missed" and not is_caller)

    return {
        "id": call.name,
        "conversation_id": call.conversation,
        "user": other_user,
        "direction": direction,
        "status": call.status,
        "is_missed": is_missed,
        "call_type": call.call_type,
        "duration": call.duration,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "created_at": call.creation,
    }


def _fetch_users(users: list[str]):
    """
    Fetch user identity details in bulk.
    """
    if not users:
        return {}

    user_rows = frappe.get_all(
        "User",
        filters={"name": ["in", users]},
        fields=["name", "full_name", "user_image"],
    )

    return {u.name: u for u in user_rows}


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

    limit = int(kwargs.get("limit") or 20)
    offset = int(kwargs.get("offset") or 0)
    conversation_id = kwargs.get("conversation_id")
    filter_type = (kwargs.get("type") or "all").strip().lower()

    # Validate filter type
    if filter_type not in ("all", "incoming", "outgoing", "missed"):
        return fail("Invalid type.", code="VALIDATION_ERROR")

    try:
        filters = []

        # Optional conversation filter
        if conversation_id:
            filters.append(["conversation", "=", conversation_id])

        # TYPE FILTERING
        if filter_type == "incoming":
            filters.append(["receiver", "=", current_user])

        elif filter_type == "outgoing":
            filters.append(["caller", "=", current_user])

        elif filter_type == "missed":
            filters.append(["receiver", "=", current_user])
            filters.append(["status", "=", "missed"])

        # Fetch calls
        calls = frappe.get_all(
            "AOS Call",
            filters=filters,
            or_filters=[
                ["caller", "=", current_user],
                ["receiver", "=", current_user],
            ] if filter_type == "all" else None,
            fields=[
                "name",
                "conversation",
                "caller",
                "receiver",
                "status",
                "call_type",
                "duration",
                "started_at",
                "ended_at",
                "creation",
            ],
            order_by="creation desc",
            limit_start=offset,
            limit_page_length=limit,
        )

        if not calls:
            return ok("Calls fetched.", data=[])

        # Collect other users
        other_users = set()

        for c in calls:
            other_users.add(c.receiver if c.caller == current_user else c.caller)

        user_map = _fetch_users(list(other_users))

        results = []

        for c in calls:
            item = _serialize_call_row(c, current_user)

            other = item["user"]
            user = user_map.get(other)

            item.update(
                {
                    "display_name": user.full_name if user else other,
                    "avatar": user.user_image if user else None,
                }
            )

            results.append(item)

        return ok("Calls fetched.", data=results)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Calls Failed",
        )
        return fail("Failed to fetch calls.", code="INTERNAL_ERROR")
