"""
Call background tasks.

Handles:
- per-call missed call timeout
- missed call cleanup detection
"""

from __future__ import annotations
import time

import frappe
from frappe.utils import add_to_date, now_datetime

from aos.api.calls.realtime import publish_call_not_answered
from aos.api.calls.utils import upsert_call_system_message
from aos.services.calls.livekit import cleanup_room, enqueue_room_cleanup, pending_cleanup_call_ids
from aos.services.calls.observability import call_log
from aos.services.calls.policy import ensure_call_interaction_allowed
from aos.services.calls.reconciliation import (
    active_room_candidates,
    reconcile_active_policy,
    reconcile_active_room,
)
from aos.services.notification_service import NotificationService


# CONSTANTS
CALL_TIMEOUT_SECONDS = 30
CALL_CLEANUP_TIMEOUT_SECONDS = 90
BATCH_SIZE = 100


def _log_error(title: str):
    # Do not persist raw provider/database exceptions or token-bearing traces.
    frappe.log_error("Unexpected Calls background-task failure.", title)


def _safe_delay_seconds(delay_seconds=None) -> int:
    """
    Normalize timeout delay.

    The timeout job receives delay_seconds from call.py.
    If missing or invalid, fall back to CALL_TIMEOUT_SECONDS.
    """
    try:
        value = int(delay_seconds if delay_seconds is not None else CALL_TIMEOUT_SECONDS)
    except Exception:
        value = CALL_TIMEOUT_SECONDS

    return max(0, value)


def _mark_call_as_missed(call_name: str, ended_at) -> bool:
    """
    Atomically mark call as missed.

    Returns True only if this task actually changed the row.
    Prevents race with accept/reject/cancel/end.
    """
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET
            status = 'missed',
            ended_at = %s,
            is_active = 0,
            room_cleanup_pending = 1,
            rtc_missing_since = NULL
        WHERE name = %s
          AND status IN ('initiated', 'ringing')
          AND is_active = 1
        """,
        (ended_at, call_name),
    )

    return frappe.db._cursor.rowcount > 0


def _reload_call(call_name: str):
    return frappe.get_doc("AOS Call", call_name)


def _handle_missed_call_side_effects(call):
    """
    Run missed-call side effects independently so one failure
    does not block the others.

    Expects a fresh call document after status has already been updated
    to missed.
    """
    try:
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content="📞 Missed call",
        )
    except Exception:
        _log_error(f"AOS Missed Call System Message Failed: {call.name}")

    # If the relationship/account became ineligible while ringing, persist the
    # authoritative missed state/history but do not deliver a new communication
    # event across the newly forbidden boundary.
    policy_error = ensure_call_interaction_allowed(
        call,
        call.receiver,
        action="receive calls from",
    )
    if not policy_error:
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

    enqueue_room_cleanup(call.name)
    call_log(
        "missed",
        outcome="success",
        reason="timeout" if not policy_error else "timeout_delivery_suppressed",
    )


def _mark_missed_and_run_side_effects(call_name: str, ended_at=None) -> bool:
    """
    Shared missed-call finalizer.

    Used by:
    - per-call timeout job
    - cron cleanup job

    Returns True only if the call was actually transitioned to missed.
    Safe to run multiple times.
    """
    ended_at = ended_at or now_datetime()

    updated = _mark_call_as_missed(call_name, ended_at)

    if not updated:
        return False

    fresh_call = _reload_call(call_name)

    _handle_missed_call_side_effects(fresh_call)

    return True


def _get_expired_initiated_calls(cutoff):
    """
    Calls that were created but never reached ringing before cleanup timeout.
    """
    return frappe.get_all(
        "AOS Call",
        filters={
            "status": "initiated",
            "is_active": 1,
            "creation": ["<=", cutoff],
        },
        fields=[
            "name",
            "creation",
        ],
        order_by="creation asc",
        limit=BATCH_SIZE,
    )


def _get_expired_ringing_calls(cutoff):
    """
    Calls that reached ringing but were not answered/rejected/cancelled
    before cleanup timeout.
    """
    return frappe.get_all(
        "AOS Call",
        filters={
            "status": "ringing",
            "is_active": 1,
            "ringing_at": ["<=", cutoff],
        },
        fields=[
            "name",
            "ringing_at",
        ],
        order_by="ringing_at asc",
        limit=BATCH_SIZE,
    )


def _get_expired_calls(cutoff):
    """
    Fetch expired initiated and ringing calls.

    Dedupe by name defensively, even though the two statuses are mutually
    exclusive.
    """

    rows = []
    rows.extend(_get_expired_initiated_calls(cutoff))
    rows.extend(_get_expired_ringing_calls(cutoff))

    seen = set()
    result = []

    for row in rows:
        if row.name in seen:
            continue

        seen.add(row.name)
        result.append(row)

    return result


# TASK: HANDLE SINGLE CALL TIMEOUT
def handle_call_timeout(call_id: str, delay_seconds: int | None = None):
    """
    Per-call timeout job.

    This job is enqueued immediately when a call is initiated using
    frappe.enqueue().

    Behavior:
    - Sleep for delay_seconds.
    - If the call is still initiated/ringing, mark it as missed.
    - If the call was accepted/rejected/cancelled/ended already, do nothing.
    - Atomic DB update prevents race conditions.
    - Side effects run only if this job actually changed the call state.

    This is the primary missed-call mechanism for predictable call timeout.
    The cron job remains as a fallback cleanup.
    """
    if not call_id:
        return

    try:
        delay = _safe_delay_seconds(delay_seconds)

        if delay > 0:
            time.sleep(delay)

        changed = _mark_missed_and_run_side_effects(
            call_name=call_id,
            ended_at=now_datetime(),
        )

        if changed:
            frappe.db.commit()

    except Exception:
        frappe.db.rollback()
        _log_error(f"AOS Call Timeout Failed: {call_id}")


# TASK: HANDLE MISSED CALLS
def handle_missed_calls():
    """
    Cleanup fallback.

    Mark calls as missed if not answered within cleanup timeout.

    Guarantees:
    - DB fetches only expired candidates.
    - Avoids infinite scheduler loop.
    - Uses creation for initiated calls.
    - Uses ringing_at for ringing calls.
    - Atomic DB transition prevents race conditions.
    - Side effects only run if state actually changed.
    - Side effects receive fresh call state after update.
    - Side effects are isolated for resilience.
    - Per-call commit for durability.

    The per-call timeout job should handle normal missed calls.
    This cron job catches abandoned/stuck calls if a timeout job fails,
    is delayed, or is not enqueued.
    """
    try:
        now = now_datetime()
        cutoff = add_to_date(now, seconds=-CALL_CLEANUP_TIMEOUT_SECONDS)

        calls = _get_expired_calls(cutoff)

        if not calls:
            return

        for call in calls:
            try:
                changed = _mark_missed_and_run_side_effects(
                    call_name=call.name,
                    ended_at=now,
                )

                if changed:
                    frappe.db.commit()

            except Exception:
                frappe.db.rollback()
                _log_error(f"AOS Missed Call Processing Failed: {call.name}")

    except Exception:
        frappe.db.rollback()
        _log_error("AOS Handle Missed Calls Failed")


# TASK: CLEAN UP A TERMINAL CALL ROOM
def cleanup_call_room(call_id: str):
    """Best-effort durable room cleanup; scheduler retries pending rows."""
    if not call_id:
        return
    try:
        result = cleanup_room(call_id)
        if result.get("ok"):
            frappe.db.commit()
        else:
            frappe.db.rollback()
    except Exception:
        frappe.db.rollback()
        _log_error(f"AOS Call Room Cleanup Failed: {call_id}")


def reconcile_call_rooms():
    """Retry a bounded set of terminal room cleanups every scheduler run."""
    try:
        call_ids = pending_cleanup_call_ids()
    except Exception:
        frappe.db.rollback()
        _log_error("AOS Call Room Cleanup Scan Failed")
        return

    for call_id in call_ids:
        try:
            result = cleanup_room(call_id)
            if result.get("ok"):
                frappe.db.commit()
            else:
                frappe.db.rollback()
                # Provider outage/auth failure is shared across rooms. Stop this
                # bounded scan rather than amplifying an unhealthy dependency.
                break
        except Exception:
            frappe.db.rollback()
            _log_error(f"AOS Call Room Reconcile Failed: {call_id}")


def reconcile_active_call_state():
    """Conservatively recover ongoing calls whose policy/RTC state is stale."""
    try:
        candidates = active_room_candidates()
    except Exception:
        frappe.db.rollback()
        _log_error("AOS Active Call Reconcile Scan Failed")
        return

    for candidate in candidates:
        try:
            policy_outcome = reconcile_active_policy(candidate)
            # Release any Accounts/Social/Call locks before touching LiveKit.
            frappe.db.commit()
            if policy_outcome == "failed_policy":
                continue

            room_outcome = reconcile_active_room(candidate)
            frappe.db.commit()
            if room_outcome == "dependency_failure":
                break
        except Exception:
            frappe.db.rollback()
            call_log("active_reconcile", outcome="failure", reason="unexpected")
            _log_error(f"AOS Active Call Reconcile Failed: {candidate.name}")
