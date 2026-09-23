"""Bounded Calls reconciliation against policy and LiveKit room existence.

Calls intentionally do not persist a participant/session ledger. Reconciliation
uses conservative signals only: canonical Accounts/Social policy is rechecked
under the same locks as call mutations; a LiveKit room must be observed missing
on two scans before an ongoing call uses the existing ``failed`` state; and an
authorized token issue clears that marker so reconnect wins safely.
"""

from __future__ import annotations

from datetime import timedelta
import time

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from aos.api.calls.realtime import publish_call_ended
from aos.api.calls.utils import upsert_call_system_message
from aos.services.livekit.admin import list_participants

from .livekit import enqueue_room_cleanup
from .observability import call_log
from .policy import ensure_call_interaction_allowed, lock_call_row, lock_users_for_call

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
        fields=[
            "name", "room_name", "caller", "receiver", "started_at",
            "rtc_missing_since", "rtc_last_checked_at",
        ],
        order_by="rtc_last_checked_at asc, started_at asc, name asc",
        limit=bounded,
    )


def clear_missing_room_marker(call_id: str) -> None:
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET rtc_missing_since = NULL
        WHERE name = %s AND status = 'ongoing' AND is_active = 1
          AND rtc_missing_since IS NOT NULL
        """,
        (call_id,),
    )


def _touch_checked(call_id: str, observed_at) -> None:
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET rtc_last_checked_at = %s
        WHERE name = %s AND status = 'ongoing' AND is_active = 1
        """,
        (observed_at, call_id),
    )


def _record_missing_room(call_id: str, observed_at) -> bool:
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET rtc_missing_since = COALESCE(rtc_missing_since, %s),
            rtc_last_checked_at = %s
        WHERE name = %s AND status = 'ongoing' AND is_active = 1
        """,
        (observed_at, observed_at, call_id),
    )
    return frappe.db._cursor.rowcount > 0


def _run_failed_call_side_effects(call, *, cleanup_required: bool) -> None:
    try:
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content="📞 Call ended",
        )
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Call Recovery System Message Failed")

    if cleanup_required:
        enqueue_room_cleanup(call.name)

    try:
        publish_call_ended(call, event_status="failed")
    except Exception:
        frappe.log_error("Calls operation failed.", "AOS Call Recovery Realtime Failed")


def _finalize_policy_failure(call_id: str, *, observed_at) -> bool:
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET status = 'failed', ended_at = %s,
            duration = GREATEST(0, TIMESTAMPDIFF(SECOND, COALESCE(started_at, %s), %s)),
            is_active = 0, room_cleanup_pending = 1,
            rtc_missing_since = NULL, rtc_last_checked_at = %s,
            state_version = state_version + 1
        WHERE name = %s AND status = 'ongoing' AND is_active = 1
        """,
        (observed_at, observed_at, observed_at, observed_at, call_id),
    )
    if frappe.db._cursor.rowcount == 0:
        return False
    call = frappe.get_doc("AOS Call", call_id)
    _run_failed_call_side_effects(call, cleanup_required=True)
    call_log("active_policy_reconcile", outcome="success", reason="access_revoked")
    return True


def reconcile_active_policy(candidate, *, observed_at=None) -> str:
    """End an ongoing call only after policy is rechecked under participant locks."""
    now = observed_at or now_datetime()
    initial = ensure_call_interaction_allowed(candidate, candidate.caller, action="continue a call with")
    if not initial:
        return "allowed"

    lock_users_for_call(candidate.caller, candidate.receiver)
    lock_call_row(candidate.name)
    call = frappe.get_doc("AOS Call", candidate.name)
    if call.status != "ongoing" or not int(call.is_active or 0):
        return "state_changed"

    current = ensure_call_interaction_allowed(call, call.caller, action="continue a call with")
    if not current:
        return "policy_changed"

    if _finalize_policy_failure(call.name, observed_at=now):
        return "failed_policy"
    return "state_changed"


def _finalize_missing_room(call_id: str, *, observed_at, missing_cutoff) -> bool:
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET status = 'failed', ended_at = %s,
            duration = GREATEST(0, TIMESTAMPDIFF(SECOND, COALESCE(started_at, %s), %s)),
            is_active = 0, room_cleanup_pending = 0,
            rtc_missing_since = NULL, rtc_last_checked_at = %s,
            state_version = state_version + 1
        WHERE name = %s AND status = 'ongoing' AND is_active = 1
          AND rtc_missing_since IS NOT NULL AND rtc_missing_since <= %s
        """,
        (observed_at, observed_at, observed_at, observed_at, call_id, missing_cutoff),
    )
    if frappe.db._cursor.rowcount == 0:
        return False
    call = frappe.get_doc("AOS Call", call_id)
    _run_failed_call_side_effects(call, cleanup_required=False)
    call_log("active_room_reconcile", outcome="success", reason="room_missing")
    return True


def reconcile_active_room(candidate, *, observed_at=None) -> str:
    """Observe LiveKit without locks; conditional markers resolve reconnect races."""
    now = observed_at or now_datetime()
    started = time.monotonic()
    result = list_participants(candidate.room_name)
    latency_ms = int((time.monotonic() - started) * 1000)

    if not result.ok:
        _touch_checked(candidate.name, now)
        call_log(
            "active_room_reconcile",
            outcome="failure",
            reason=result.category,
            latency_ms=latency_ms,
        )
        return "dependency_failure"

    if result.category != "not_found":
        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET rtc_missing_since = NULL, rtc_last_checked_at = %s
            WHERE name = %s AND status = 'ongoing' AND is_active = 1
            """,
            (now, candidate.name),
        )
        call_log(
            "active_room_reconcile",
            outcome="success",
            reason="room_present",
            latency_ms=latency_ms,
        )
        return "room_present"

    missing_since = getattr(candidate, "rtc_missing_since", None)
    if not missing_since:
        if _record_missing_room(candidate.name, now):
            call_log("active_room_reconcile", outcome="success", reason="room_missing_observed")
            return "missing_observed"
        return "state_changed"

    missing_dt = get_datetime(missing_since)
    cutoff = now - timedelta(seconds=ROOM_MISSING_CONFIRM_SECONDS)
    if missing_dt > cutoff:
        _touch_checked(candidate.name, now)
        return "missing_grace"

    if _finalize_missing_room(candidate.name, observed_at=now, missing_cutoff=cutoff):
        return "failed"
    return "state_changed"
