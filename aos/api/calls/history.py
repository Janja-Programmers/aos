"""Cursor-paginated per-user Calls history for direct and group calls."""
from __future__ import annotations

import json

import frappe
from frappe.utils import get_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.api.shared.user_display import get_user_display_map
from aos.services.calls.identifiers import internal_call_name

from .constants import CLEAR_CALL_HISTORY_LIMIT_PER_MINUTE_PER_USER, DELETE_CALL_LOGS_LIMIT_PER_MINUTE_PER_USER, LIST_CALLS_LIMIT_PER_MINUTE_PER_USER
from .realtime import serialize_call_for_realtime

MAX_PAGE_SIZE = 50
MAX_DELETE_BATCH = 100


def _int(value, default=20):
    try:
        return max(1, min(int(value), MAX_PAGE_SIZE))
    except Exception:
        return default


def _ids(value) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = [value]
    if not isinstance(value, (list, tuple, set)):
        value = [value] if value else []
    out, seen = [], set()
    for item in value:
        item = str(item or "").strip()
        if item and item not in seen:
            seen.add(item); out.append(item)
    return out


def _batch_participants(call_names: list[str]):
    if not call_names:
        return {}, {}
    rows = frappe.db.sql(
        """
        SELECT name,`call`,user,role,status,added_by,visible,invited_at,incoming_dispatched_at,
               ring_expires_at,ringing_at,joined_at,responded_at,left_at,creation
        FROM `tabAOS Call Participant`
        WHERE `call` IN %(calls)s
        ORDER BY `call`,creation,name
        """,
        {"calls": tuple(call_names)},
        as_dict=True,
    )
    by_call: dict[str, list] = {}
    users = set()
    for row in rows:
        by_call.setdefault(row.call, []).append(row)
        if row.user:
            users.add(row.user)
    try:
        displays = get_user_display_map(users)
    except Exception:
        displays = {}
    return by_call, displays


def list_calls_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "history", current_user), ttl_seconds=60, limit=LIST_CALLS_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")):
        return rl
    limit = _int(kwargs.get("limit"), 20)
    filter_type = str(kwargs.get("type") or "all").strip().lower()
    if filter_type not in {"all", "incoming", "outgoing", "missed"}:
        return fail("Invalid call history type.", error="VALIDATION_ERROR")
    conversation_id = str(kwargs.get("conversation_id") or "").strip() or None
    cursor_created = kwargs.get("cursor_created_at")
    cursor_call_id = str(kwargs.get("cursor_call_id") or "").strip() or None
    values = {"user": current_user, "limit": limit + 1}
    conditions = ["p.user=%(user)s", "p.visible=1"]
    if conversation_id:
        conditions.append("c.conversation=%(conversation)s"); values["conversation"] = conversation_id
    if filter_type == "incoming":
        conditions.append("c.initiator!=%(user)s")
    elif filter_type == "outgoing":
        conditions.append("c.initiator=%(user)s")
    elif filter_type == "missed":
        conditions.append("p.status='missed'")
    if bool(cursor_created) != bool(cursor_call_id):
        return fail("Invalid cursor.", error="CALL_INVALID_CURSOR")
    if cursor_created and cursor_call_id:
        cursor_name = internal_call_name(cursor_call_id)
        if not cursor_name or not frappe.db.exists(
            "AOS Call Participant", {"call": cursor_name, "user": current_user}
        ):
            return fail("Invalid cursor.", error="CALL_INVALID_CURSOR")
        values.update({"cursor_created": get_datetime(cursor_created), "cursor_name": cursor_name})
        conditions.append("(c.creation<%(cursor_created)s OR (c.creation=%(cursor_created)s AND c.name<%(cursor_name)s))")
    rows = frappe.db.sql(
        f"""
        SELECT c.*
        FROM `tabAOS Call` c
        INNER JOIN `tabAOS Call Participant` p ON p.`call`=c.name
        WHERE {' AND '.join(conditions)}
        ORDER BY c.creation DESC,c.name DESC
        LIMIT %(limit)s
        """,
        values,
        as_dict=True,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    names = [row.name for row in rows]
    participants, displays = _batch_participants(names)
    data = []
    for row in rows:
        item = serialize_call_for_realtime(row, current_user=current_user, user_summaries=displays, participants_override=participants.get(row.name, []))
        own = next((p for p in participants.get(row.name, []) if p.user == current_user), None)
        item["direction"] = "outgoing" if row.initiator == current_user else "incoming"
        item["history_status"] = own.status if own else None
        item["created_at"] = row.creation
        data.append(item)
    next_cursor = None
    if has_more and rows:
        next_cursor = {"cursor_created_at": rows[-1].creation, "cursor_call_id": rows[-1].public_id}
    return ok("Calls fetched.", data={"calls": data, "next_cursor": next_cursor})


def delete_call_logs_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "history_delete", current_user), ttl_seconds=60, limit=DELETE_CALL_LOGS_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")):
        return rl
    call_ids = _ids(kwargs.get("call_ids"))
    if not call_ids or len(call_ids) > MAX_DELETE_BATCH:
        return fail("Provide between 1 and 100 call IDs.", error="VALIDATION_ERROR")
    names = [internal_call_name(call_id) for call_id in call_ids]
    names = [name for name in names if name]
    deleted = 0
    if names:
        frappe.db.sql(
            "UPDATE `tabAOS Call Participant` SET visible=0 WHERE user=%(user)s AND visible=1 AND `call` IN %(calls)s",
            {"user": current_user, "calls": tuple(names)},
        )
        deleted = max(0, int(frappe.db._cursor.rowcount or 0))
    return ok("Call logs deleted.", data={"deleted": deleted})


def clear_call_history_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (rl := rate_limit(key=rate_limit_key("calls", "history_clear", current_user), ttl_seconds=60, limit=CLEAR_CALL_HISTORY_LIMIT_PER_MINUTE_PER_USER, message="Too many requests. Please try again shortly.")):
        return rl
    total = 0
    while True:
        names = frappe.get_all("AOS Call Participant", filters={"user": current_user, "visible": 1}, pluck="name", order_by="creation asc,name asc", limit=500)
        if not names:
            break
        frappe.db.sql("UPDATE `tabAOS Call Participant` SET visible=0 WHERE name IN %(names)s", {"names": tuple(names)})
        total += len(names)
        if len(names) < 500:
            break
    return ok("Call history cleared.", data={"cleared": total})
