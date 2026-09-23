"""Bounded Calls reconciliation against Accounts/Social policy and LiveKit."""
from __future__ import annotations

from datetime import timedelta
import time

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from aos.api.calls.realtime import publish_call_ended, publish_participant_left
from aos.services.livekit.admin import list_participants

from .livekit import enqueue_room_cleanup
from .observability import call_log
from .participants import joined_users, participant_rows
from .policy import ensure_interaction_allowed, ensure_user_available, lock_call_participants

ACTIVE_ROOM_RECONCILE_BATCH_SIZE = 20
ACTIVE_ROOM_MIN_AGE_SECONDS = 300
ROOM_MISSING_CONFIRM_SECONDS = 300


def active_room_candidates(*, now=None, limit: int = ACTIVE_ROOM_RECONCILE_BATCH_SIZE):
    observed_at = now or now_datetime()
    cutoff = add_to_date(observed_at, seconds=-ACTIVE_ROOM_MIN_AGE_SECONDS)
    bounded = max(1, min(int(limit or ACTIVE_ROOM_RECONCILE_BATCH_SIZE), ACTIVE_ROOM_RECONCILE_BATCH_SIZE))
    return frappe.get_all(
        "AOS Call",
        filters={"status": "ongoing", "is_active": 1, "started_at": ["<=", cutoff]},
        fields=["name", "room_name", "initiator", "call_mode", "started_at", "rtc_missing_since", "rtc_last_checked_at"],
        order_by="rtc_last_checked_at asc, started_at asc, name asc",
        limit=bounded,
    )


def clear_missing_room_marker(call_id: str) -> None:
    frappe.db.sql("UPDATE `tabAOS Call` SET rtc_missing_since=NULL WHERE name=%s AND status='ongoing' AND is_active=1 AND rtc_missing_since IS NOT NULL", (call_id,))


def _fail_call(call_id: str, observed_at, *, cleanup: bool) -> bool:
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET status='failed', ended_at=%s,
            duration=GREATEST(0,TIMESTAMPDIFF(SECOND,COALESCE(started_at,%s),%s)),
            is_active=0, room_cleanup_pending=%s, rtc_missing_since=NULL,
            rtc_last_checked_at=%s, state_version=state_version+1
        WHERE name=%s AND status='ongoing' AND is_active=1
        """,
        (observed_at, observed_at, observed_at, 1 if cleanup else 0, observed_at, call_id),
    )
    if frappe.db._cursor.rowcount == 0:
        return False
    frappe.db.sql("UPDATE `tabAOS Call Participant` SET status='failed', left_at=COALESCE(left_at,%s) WHERE `call`=%s AND status IN ('invited','ringing','joined')", (observed_at, call_id))
    call = frappe.get_doc("AOS Call", call_id)
    if cleanup:
        enqueue_room_cleanup(call.name)
    publish_call_ended(call, event_status="failed")
    return True


def reconcile_active_policy(candidate, *, observed_at=None) -> str:
    now = observed_at or now_datetime()
    lock_call_participants(candidate.name)
    call = frappe.get_doc("AOS Call", candidate.name)
    if call.status != "ongoing" or not int(call.is_active or 0):
        return "state_changed"
    if ensure_user_available(call.initiator, target=False):
        if call.call_mode == "direct":
            return "failed_policy" if _fail_call(call.name, now, cleanup=True) else "state_changed"
        frappe.db.sql(
            "UPDATE `tabAOS Call Participant` SET status='left', left_at=COALESCE(left_at,%s) "
            "WHERE `call`=%s AND user=%s AND status='joined'",
            (now, call.name, call.initiator),
        )
        frappe.db.sql("UPDATE `tabAOS Call` SET state_version=state_version+1 WHERE name=%s", (call.name,))

    revoked: list[str] = []
    for row in participant_rows(call.name):
        if row.role == "initiator" or row.status != "joined":
            continue
        if not ensure_user_available(call.initiator, target=False) and ensure_interaction_allowed(current_user=call.initiator, peer_user=row.user, action="continue a call with"):
            revoked.append(row.user)
    if not revoked:
        return "allowed"

    if call.call_mode == "direct":
        return "failed_policy" if _fail_call(call.name, now, cleanup=True) else "state_changed"

    for user in revoked:
        frappe.db.sql("UPDATE `tabAOS Call Participant` SET status='left', left_at=COALESCE(left_at,%s) WHERE `call`=%s AND user=%s AND status='joined'", (now, call.name, user))
        publish_participant_left(call, user)
    frappe.db.sql("UPDATE `tabAOS Call` SET state_version=state_version+1 WHERE name=%s", (call.name,))
    if len(joined_users(call.name)) == 0:
        return "failed_policy" if _fail_call(call.name, now, cleanup=True) else "state_changed"
    call_log("active_policy_reconcile", outcome="success", reason="participants_revoked")
    return "participants_revoked"


def _touch_checked(call_id: str, observed_at) -> None:
    frappe.db.sql("UPDATE `tabAOS Call` SET rtc_last_checked_at=%s WHERE name=%s AND status='ongoing' AND is_active=1", (observed_at, call_id))


def reconcile_active_room(candidate, *, observed_at=None) -> str:
    now = observed_at or now_datetime()
    started = time.monotonic()
    result = list_participants(candidate.room_name)
    latency = int((time.monotonic() - started) * 1000)
    if not result.ok:
        _touch_checked(candidate.name, now)
        call_log("active_room_reconcile", outcome="failure", reason=result.category, latency_ms=latency)
        return "dependency_failure"
    if result.category != "not_found":
        frappe.db.sql("UPDATE `tabAOS Call` SET rtc_missing_since=NULL,rtc_last_checked_at=%s WHERE name=%s AND status='ongoing' AND is_active=1", (now, candidate.name))
        return "room_present"
    missing_since = getattr(candidate, "rtc_missing_since", None)
    if not missing_since:
        frappe.db.sql("UPDATE `tabAOS Call` SET rtc_missing_since=%s,rtc_last_checked_at=%s WHERE name=%s AND status='ongoing' AND is_active=1", (now, now, candidate.name))
        return "missing_observed"
    cutoff = now - timedelta(seconds=ROOM_MISSING_CONFIRM_SECONDS)
    if get_datetime(missing_since) > cutoff:
        _touch_checked(candidate.name, now)
        return "missing_grace"
    return "failed" if _fail_call(candidate.name, now, cleanup=False) else "state_changed"
