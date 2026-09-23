"""Durable multi-participant call membership/state helpers."""
from __future__ import annotations

from collections.abc import Iterable

import frappe
from frappe.utils import now_datetime

ACTIVE_PARTICIPANT_STATUSES = {"invited", "ringing", "joined"}
JOINED_PARTICIPANT_STATUSES = {"joined"}
TERMINAL_PARTICIPANT_STATUSES = {"declined", "missed", "left", "failed", "cancelled"}


def participant_rows(call_name: str, *, include_hidden: bool = True):
    filters = {"call": call_name}
    if not include_hidden:
        filters["visible"] = 1
    return frappe.get_all(
        "AOS Call Participant",
        filters=filters,
        fields=[
            "name", "call", "user", "role", "status", "added_by", "visible",
            "invited_at", "incoming_dispatched_at", "ring_expires_at", "ringing_at",
            "joined_at", "responded_at", "left_at", "creation",
        ],
        order_by="creation asc, name asc",
        limit=100,
    )


def participant_for_user(call_name: str, user: str, *, for_update: bool = False):
    if for_update:
        rows = frappe.db.sql(
            """
            SELECT name, `call`, user, role, status, added_by, visible,
                   invited_at, incoming_dispatched_at, ring_expires_at, ringing_at,
                   joined_at, responded_at, left_at, creation
            FROM `tabAOS Call Participant`
            WHERE `call`=%s AND user=%s
            LIMIT 1 FOR UPDATE
            """,
            (call_name, user),
            as_dict=True,
        )
        return rows[0] if rows else None
    return frappe.db.get_value(
        "AOS Call Participant",
        {"call": call_name, "user": user},
        [
            "name", "call", "user", "role", "status", "added_by", "visible",
            "invited_at", "incoming_dispatched_at", "ring_expires_at", "ringing_at",
            "joined_at", "responded_at", "left_at", "creation",
        ],
        as_dict=True,
    )


def users_for_call(call_name: str, *, statuses: Iterable[str] | None = None) -> list[str]:
    filters: dict = {"call": call_name}
    if statuses:
        filters["status"] = ["in", list(statuses)]
    return [
        str(user)
        for user in frappe.get_all(
            "AOS Call Participant",
            filters=filters,
            pluck="user",
            order_by="creation asc, name asc",
            limit=100,
        )
        if user
    ]


def participant_user_set(call_name: str) -> set[str]:
    return set(users_for_call(call_name))


def create_participant(*, call_name: str, user: str, role: str, status: str, added_by: str | None = None, invited_at=None):
    doc = frappe.new_doc("AOS Call Participant")
    doc.call = call_name
    doc.user = user
    doc.role = role
    doc.status = status
    doc.added_by = added_by
    doc.visible = 1
    doc.invited_at = invited_at or now_datetime()
    if status == "joined":
        doc.joined_at = invited_at or now_datetime()
    doc.insert(ignore_permissions=True)
    return doc


def set_participant_count(call_name: str) -> int:
    count = int(frappe.db.count("AOS Call Participant", {"call": call_name}) or 0)
    frappe.db.set_value("AOS Call", call_name, "participant_count", count, update_modified=False)
    return count


def joined_users(call_name: str) -> list[str]:
    return users_for_call(call_name, statuses=("joined",))


def pending_users(call_name: str) -> list[str]:
    return users_for_call(call_name, statuses=("invited", "ringing"))


def non_initiator_rows(call_name: str):
    return frappe.get_all(
        "AOS Call Participant",
        filters={"call": call_name, "role": "participant"},
        fields=["name", "user", "status", "ring_expires_at", "incoming_dispatched_at"],
        order_by="creation asc, name asc",
        limit=100,
    )


def all_invitees_terminal(call_name: str) -> bool:
    rows = non_initiator_rows(call_name)
    return bool(rows) and all(row.status in TERMINAL_PARTICIPANT_STATUSES for row in rows)


def any_invitee_joined(call_name: str) -> bool:
    return bool(
        frappe.db.exists(
            "AOS Call Participant",
            {"call": call_name, "role": "participant", "status": "joined"},
        )
    )


def lock_participant_rows(call_name: str) -> None:
    frappe.db.sql(
        """
        SELECT name FROM `tabAOS Call Participant`
        WHERE `call`=%s ORDER BY user, name FOR UPDATE
        """,
        (call_name,),
    )
