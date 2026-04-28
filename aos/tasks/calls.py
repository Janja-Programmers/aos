"""
Call background tasks.

Handles:
- missed call detection
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, now_datetime

from aos.api.calls.realtime import publish_call_not_answered
from aos.api.calls.utils import upsert_call_system_message
from aos.services.notification_service import NotificationService


# CONSTANTS
MISSED_CALL_TIMEOUT_SECONDS = 30
BATCH_SIZE = 100


def _log_error(title: str):
    frappe.log_error(frappe.get_traceback(), title)


def _mark_call_as_missed(call_name: str, ended_at) -> bool:
    """
    Atomically mark call as missed.

    Returns True only if this task actually changed the row.
    Prevents race with accept/reject/cancel/end.
    """
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET status = 'missed',
            ended_at = %s,
            is_active = 0
        WHERE name = %s
          AND status IN ('initiated', 'ringing')
          AND is_active = 1
        """,
        (ended_at, call_name),
    )
    return frappe.db._cursor.rowcount > 0


def _handle_missed_call_side_effects(call):
    """
    Run missed-call side effects independently so one failure
    does not block the others.
    """
    try:
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content="📞 Missed call",
        )
    except Exception:
        _log_error(f"AOS Missed Call System Message Failed: {call.name}")

    try:
        publish_call_not_answered(call)
    except Exception:
        _log_error(f"AOS Missed Call Realtime Failed: {call.name}")

    try:
        NotificationService.notify_missed_call(
            user=call.receiver,
            caller=call.caller,
            call_id=call.name,
        )
    except Exception:
        _log_error(f"AOS Missed Call Notification Failed: {call.name}")


def _get_expired_initiated_calls(cutoff):
    return frappe.get_all(
        "AOS Call",
        filters={
            "status": "initiated",
            "is_active": 1,
            "creation": ["<=", cutoff],
        },
        fields=[
            "name",
            "conversation",
            "caller",
            "receiver",
            "status",
            "creation",
            "ringing_at",
        ],
        order_by="creation asc",
        limit=BATCH_SIZE,
    )


def _get_expired_ringing_calls(cutoff):
    return frappe.get_all(
        "AOS Call",
        filters={
            "status": "ringing",
            "is_active": 1,
            "ringing_at": ["<=", cutoff],
        },
        fields=[
            "name",
            "conversation",
            "caller",
            "receiver",
            "status",
            "creation",
            "ringing_at",
        ],
        order_by="ringing_at asc",
        limit=BATCH_SIZE,
    )


# TASK: HANDLE MISSED CALLS
def handle_missed_calls():
    """
    Mark calls as missed if not answered within timeout.

    Production guarantees:
    - DB fetches only expired candidates
    - Avoids infinite scheduler loop
    - Uses creation for initiated calls
    - Uses ringing_at for ringing calls
    - Atomic DB transition prevents race conditions
    - Side effects only run if state actually changed
    - Side effects isolated for resilience
    - Per-call commit for durability
    """
    try:
        now = now_datetime()
        cutoff = add_to_date(now, seconds=-MISSED_CALL_TIMEOUT_SECONDS)

        calls = []
        calls.extend(_get_expired_initiated_calls(cutoff))
        calls.extend(_get_expired_ringing_calls(cutoff))

        if not calls:
            return

        for call in calls:
            try:
                updated = _mark_call_as_missed(call.name, now)

                if not updated:
                    continue

                _handle_missed_call_side_effects(call)
                frappe.db.commit()

            except Exception:
                frappe.db.rollback()
                _log_error(f"AOS Missed Call Processing Failed: {call.name}")

    except Exception:
        frappe.db.rollback()
        _log_error("AOS Handle Missed Calls Failed")
