"""Calls background tasks built on the shared LiveKit/Notifications infrastructure."""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, now_datetime

from aos.api.calls.constants import CALL_RING_TIMEOUT_SECONDS
from aos.api.calls.realtime import (
    publish_call_failed_to_caller,
    publish_call_not_answered,
    publish_call_ready,
    publish_incoming_call,
    serialize_call_for_realtime,
)
from aos.api.calls.utils import upsert_call_system_message
from aos.services.calls.identifiers import public_call_id
from aos.services.calls.livekit import (
    cleanup_room,
    enqueue_room_cleanup,
    enqueue_room_provisioning,
    pending_cleanup_call_ids,
    provision_call_room as provision_livekit_room,
)
from aos.services.calls.observability import call_log
from aos.services.calls.policy import (
    ensure_call_interaction_allowed,
    lock_call_row,
    lock_users_for_call,
)
from aos.services.calls.reconciliation import (
    active_room_candidates,
    reconcile_active_policy,
    reconcile_active_room,
)
from aos.services.notifications.service import NotificationService


# Bounded scheduler work. Calls are keyset/index driven; there is no per-call
# sleeping worker and no process-local state required for correctness.
MISSED_CALL_BATCH_SIZE = 200
MISSED_CALL_MAX_BATCHES_PER_RUN = 5
PROVISION_RECOVERY_BATCH_SIZE = 100
PROVISION_RECOVERY_AGE_SECONDS = 60


def _log_error(title: str):
    # Do not persist raw provider/database exceptions or token-bearing traces.
    frappe.log_error("Unexpected Calls background-task failure.", title)


def _reload_call(call_name: str):
    return frappe.get_doc("AOS Call", call_name)


def _terminalize_provision_failure(call_name: str, *, ended_at) -> bool:
    """Fail a still-pending call without overwriting a concurrent user action."""
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET
            status = 'failed',
            ended_at = %s,
            is_active = 0,
            room_cleanup_pending = 1,
            rtc_missing_since = NULL,
            state_version = state_version + 1
        WHERE name = %s
          AND status = 'initiated'
          AND is_active = 1
          AND incoming_dispatched_at IS NULL
        """,
        (ended_at, call_name),
    )
    return frappe.db._cursor.rowcount > 0


def _run_provision_failure_side_effects(call) -> None:
    try:
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content="📞 Call failed",
        )
    except Exception:
        _log_error(f"AOS Call Provision Failure Message Failed: {call.name}")

    enqueue_room_cleanup(call.name)

    try:
        publish_call_failed_to_caller(call)
    except Exception:
        _log_error(f"AOS Call Provision Failure Realtime Failed: {call.name}")


def _incoming_push_payload(call) -> dict:
    payload = serialize_call_for_realtime(
        call,
        current_user=call.receiver,
        event_status="incoming",
        actor=call.caller,
    )
    payload.update(
        {
            "event": "aos_incoming_call",
            "type": "incoming_call",
            "notification_type": "incoming_call",
        }
    )
    return payload


# TASK: PROVISION SHARED LIVEKIT ROOM + DISPATCH INCOMING CALL
def provision_call_room(call_name: str):
    """Provision a 1:1 room without holding DB locks across provider I/O.

    The durable row remains ``initiated`` while the shared LiveKit admin layer
    provisions the room. Incoming realtime/push and caller readiness are emitted
    only after room existence is confirmed and policy/state are revalidated.
    """
    call_name = str(call_name or "").strip()
    if not call_name:
        return

    try:
        candidate = frappe.db.get_value(
            "AOS Call",
            call_name,
            [
                "name",
                "room_name",
                "caller",
                "receiver",
                "status",
                "is_active",
                "incoming_dispatched_at",
            ],
            as_dict=True,
        )
        if not candidate:
            return
        if candidate.status != "initiated" or not int(candidate.is_active or 0):
            return
        if candidate.incoming_dispatched_at:
            return

        # Release the read transaction before network I/O. No database row lock
        # is held while calling the shared LiveKit admin service.
        frappe.db.commit()
        result = provision_livekit_room(candidate.room_name)

        if not result.ok:
            lock_users_for_call(candidate.caller, candidate.receiver)
            lock_call_row(call_name)
            if _terminalize_provision_failure(call_name, ended_at=now_datetime()):
                call = _reload_call(call_name)
                _run_provision_failure_side_effects(call)
                call_log("room_provision_finalize", outcome="failure", reason=result.category)
                frappe.db.commit()
            else:
                frappe.db.rollback()
            return

        # Revalidate canonical policy and durable state after provider I/O. This
        # closes accept/cancel/block/account-state races without a long DB tx.
        lock_users_for_call(candidate.caller, candidate.receiver)
        lock_call_row(call_name)
        call = _reload_call(call_name)

        if call.status != "initiated" or not int(call.is_active or 0):
            # The room may have been created after a concurrent cancellation.
            # Re-arm cleanup even if an earlier not-found cleanup already ran.
            frappe.db.sql(
                """
                UPDATE `tabAOS Call`
                SET room_cleanup_pending = 1
                WHERE name = %s
                """,
                (call.name,),
            )
            enqueue_room_cleanup(call.name)
            frappe.db.commit()
            return

        if call.incoming_dispatched_at:
            frappe.db.commit()
            return

        policy_error = ensure_call_interaction_allowed(
            call,
            call.caller,
            action="call",
        )
        if policy_error:
            if _terminalize_provision_failure(call.name, ended_at=now_datetime()):
                call = _reload_call(call.name)
                _run_provision_failure_side_effects(call)
                call_log("room_provision_finalize", outcome="failure", reason="policy_revoked")
                frappe.db.commit()
            else:
                frappe.db.rollback()
            return

        dispatched_at = now_datetime()
        ring_expires_at = add_to_date(dispatched_at, seconds=CALL_RING_TIMEOUT_SECONDS)
        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET
                rtc_provisioned_at = COALESCE(rtc_provisioned_at, %s),
                incoming_dispatched_at = %s,
                ring_expires_at = %s,
                state_version = state_version + 1
            WHERE name = %s
              AND status = 'initiated'
              AND is_active = 1
              AND incoming_dispatched_at IS NULL
            """,
            (dispatched_at, dispatched_at, ring_expires_at, call.name),
        )
        if frappe.db._cursor.rowcount == 0:
            frappe.db.rollback()
            return

        call = _reload_call(call.name)

        # Do not surface a chat-level ringing state until the RTC room is
        # proven ready. This stays in the same DB transaction as readiness.
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content="📞 Calling...",
        )

        # Realtime is after-commit and the Notifications service uses its
        # hardened idempotency/outbox path. Public call IDs are opaque.
        publish_call_ready(call)
        publish_incoming_call(call, call.receiver)
        NotificationService.notify_incoming_call(
            user=call.receiver,
            caller=call.caller,
            call_id=public_call_id(call),
            call_type=call.call_type,
            payload=_incoming_push_payload(call),
        )
        call_log("incoming_dispatch", outcome="success")
        frappe.db.commit()

    except Exception:
        frappe.db.rollback()
        call_log("room_provision_finalize", outcome="failure", reason="unexpected")
        _log_error(f"AOS Call Room Provision Failed: {call_name}")


def _mark_call_as_missed(call_name: str, ended_at) -> bool:
    """Atomically mark one dispatched unanswered call as missed."""
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET
            status = 'missed',
            ended_at = %s,
            is_active = 0,
            room_cleanup_pending = 1,
            rtc_missing_since = NULL,
            state_version = state_version + 1
        WHERE name = %s
          AND status IN ('initiated', 'ringing')
          AND is_active = 1
          AND incoming_dispatched_at IS NOT NULL
          AND ring_expires_at IS NOT NULL
          AND ring_expires_at <= %s
        """,
        (ended_at, call_name, ended_at),
    )
    return frappe.db._cursor.rowcount > 0


def _handle_missed_call_side_effects(call):
    try:
        upsert_call_system_message(
            call_id=call.name,
            conversation_id=call.conversation,
            content="📞 Missed call",
        )
    except Exception:
        _log_error(f"AOS Missed Call System Message Failed: {call.name}")

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
                call_id=public_call_id(call),
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
    ended_at = ended_at or now_datetime()
    if not _mark_call_as_missed(call_name, ended_at):
        return False
    _handle_missed_call_side_effects(_reload_call(call_name))
    return True


def _get_expired_calls(now):
    return frappe.get_all(
        "AOS Call",
        filters={
            "status": ["in", ["initiated", "ringing"]],
            "is_active": 1,
            "incoming_dispatched_at": ["is", "set"],
            "ring_expires_at": ["<=", now],
        },
        fields=["name", "ring_expires_at"],
        order_by="ring_expires_at asc, creation asc, name asc",
        limit=MISSED_CALL_BATCH_SIZE,
    )


def _recover_pending_provisioning(now) -> None:
    cutoff = add_to_date(now, seconds=-PROVISION_RECOVERY_AGE_SECONDS)
    rows = frappe.get_all(
        "AOS Call",
        filters={
            "status": "initiated",
            "is_active": 1,
            "incoming_dispatched_at": ["is", "not set"],
            "creation": ["<=", cutoff],
        },
        pluck="name",
        order_by="creation asc, name asc",
        limit=PROVISION_RECOVERY_BATCH_SIZE,
    )
    for call_name in rows:
        try:
            enqueue_room_provisioning(call_name)
        except Exception:
            # One queue/Redis failure must not prevent timeout handling for the
            # rest of this scheduler run. The next minute tick retries recovery.
            _log_error(f"AOS Call Provision Recovery Enqueue Failed: {call_name}")


# TASK: HANDLE MISSED CALLS + RECOVER LOST PROVISIONING ENQUEUES
def handle_missed_calls():
    """Bounded minute scheduler for durable unanswered-call expiry."""
    try:
        now = now_datetime()
        _recover_pending_provisioning(now)
        for _ in range(MISSED_CALL_MAX_BATCHES_PER_RUN):
            calls = _get_expired_calls(now)
            if not calls:
                break

            changed_in_batch = 0
            for call in calls:
                try:
                    if _mark_missed_and_run_side_effects(call.name, ended_at=now):
                        changed_in_batch += 1
                    frappe.db.commit()
                except Exception:
                    frappe.db.rollback()
                    _log_error(f"AOS Missed Call Processing Failed: {call.name}")

            if len(calls) < MISSED_CALL_BATCH_SIZE or changed_in_batch == 0:
                break

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
