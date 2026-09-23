"""Calls background tasks using shared LiveKit/Notifications infrastructure."""
from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from aos.api.calls.constants import CALL_RING_TIMEOUT_SECONDS
from aos.api.calls.realtime import (
    publish_call_failed_to_initiator,
    publish_call_ready,
    publish_incoming_call,
    publish_participant_missed,
    serialize_call_for_realtime,
)
from aos.api.calls.utils import upsert_call_system_message
from aos.services.calls.identifiers import public_call_id
from aos.services.calls.livekit import cleanup_room, enqueue_room_cleanup, enqueue_room_provisioning, pending_cleanup_call_ids, provision_call_room as provision_livekit_room, room_capacity_for_call
from aos.services.calls.observability import call_log
from aos.services.calls.participants import all_invitees_terminal, any_invitee_joined, participant_rows, users_for_call
from aos.services.calls.policy import ensure_interaction_allowed, ensure_user_available, lock_call_participants
from aos.services.calls.reconciliation import active_room_candidates, reconcile_active_policy, reconcile_active_room
from aos.services.notifications.service import NotificationService

MISSED_CALL_BATCH_SIZE = 200
MISSED_CALL_MAX_BATCHES_PER_RUN = 5
PROVISION_RECOVERY_BATCH_SIZE = 100
PROVISION_RECOVERY_AGE_SECONDS = 60


def _log_error(title: str):
    frappe.log_error("Unexpected Calls background-task failure.", title)


def _reload(call_name: str):
    return frappe.get_doc("AOS Call", call_name)


def _maybe_message(call, content: str):
    if call.conversation:
        upsert_call_system_message(call_id=call.name, conversation_id=call.conversation, content=content)


def _terminalize_provision_failure(call_name: str, ended_at) -> bool:
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET status='failed', ended_at=%s, is_active=0, room_cleanup_pending=1,
            rtc_missing_since=NULL, state_version=state_version+1
        WHERE name=%s AND status='initiated' AND is_active=1 AND rtc_provisioned_at IS NULL
        """,
        (ended_at, call_name),
    )
    if frappe.db._cursor.rowcount == 0:
        return False
    frappe.db.sql(
        "UPDATE `tabAOS Call Participant` SET status='failed', responded_at=COALESCE(responded_at,%s) WHERE `call`=%s AND status IN ('invited','ringing','joined')",
        (ended_at, call_name),
    )
    return True


def _provision_failure_side_effects(call):
    _maybe_message(call, "📞 Call failed")
    enqueue_room_cleanup(call.name)
    try:
        publish_call_failed_to_initiator(call)
    except Exception:
        _log_error(f"AOS Call Provision Failure Realtime Failed: {call.name}")


def _incoming_payload(call, user: str) -> dict:
    payload = serialize_call_for_realtime(call, current_user=user, event_status="incoming", actor=call.initiator)
    payload.update({"event": "aos_incoming_call", "type": "incoming_call", "notification_type": "incoming_call"})
    return payload


def provision_call_room(call_name: str):
    """Provision once, then fan out durable incoming invitations after revalidation."""
    call_name = str(call_name or "").strip()
    if not call_name:
        return
    try:
        candidate = frappe.db.get_value(
            "AOS Call", call_name,
            ["name", "room_name", "initiator", "call_mode", "status", "is_active", "rtc_provisioned_at", "max_participants"],
            as_dict=True,
        )
        if not candidate or candidate.status != "initiated" or not int(candidate.is_active or 0) or candidate.rtc_provisioned_at:
            return
        frappe.db.commit()
        result = provision_livekit_room(candidate.room_name, max_participants=room_capacity_for_call(candidate.call_mode))
        if not result.ok:
            lock_call_participants(call_name)
            if _terminalize_provision_failure(call_name, now_datetime()):
                call = _reload(call_name)
                _provision_failure_side_effects(call)
                frappe.db.commit()
            else:
                frappe.db.rollback()
            return

        lock_call_participants(call_name)
        call = _reload(call_name)
        if call.status != "initiated" or not int(call.is_active or 0):
            frappe.db.sql("UPDATE `tabAOS Call` SET room_cleanup_pending=1 WHERE name=%s", (call.name,))
            enqueue_room_cleanup(call.name)
            frappe.db.commit()
            return
        if call.rtc_provisioned_at:
            frappe.db.commit()
            return
        if ensure_user_available(call.initiator, target=False):
            if _terminalize_provision_failure(call.name, now_datetime()):
                call = _reload(call.name)
                _provision_failure_side_effects(call)
                frappe.db.commit()
            else:
                frappe.db.rollback()
            return

        now = now_datetime()
        valid_targets: list[str] = []
        for row in participant_rows(call.name):
            if row.role == "initiator" or row.status != "invited":
                continue
            if ensure_interaction_allowed(current_user=call.initiator, peer_user=row.user, action="call"):
                frappe.db.sql("UPDATE `tabAOS Call Participant` SET status='failed', responded_at=%s WHERE name=%s AND status='invited'", (now, row.name))
                continue
            valid_targets.append(row.user)
        if not valid_targets:
            if _terminalize_provision_failure(call.name, now):
                call = _reload(call.name)
                _provision_failure_side_effects(call)
                frappe.db.commit()
            else:
                frappe.db.rollback()
            return

        expires = add_to_date(now, seconds=CALL_RING_TIMEOUT_SECONDS)
        frappe.db.sql("UPDATE `tabAOS Call` SET rtc_provisioned_at=COALESCE(rtc_provisioned_at,%s), state_version=state_version+1 WHERE name=%s AND status='initiated' AND is_active=1 AND rtc_provisioned_at IS NULL", (now, call.name))
        if frappe.db._cursor.rowcount == 0:
            frappe.db.rollback()
            return
        for user in valid_targets:
            frappe.db.sql("UPDATE `tabAOS Call Participant` SET incoming_dispatched_at=%s, ring_expires_at=%s WHERE `call`=%s AND user=%s AND status='invited' AND incoming_dispatched_at IS NULL", (now, expires, call.name, user))

        call = _reload(call.name)
        _maybe_message(call, "📞 Calling...")
        publish_call_ready(call)
        for user in valid_targets:
            publish_incoming_call(call, user, actor=call.initiator)
            NotificationService.notify_incoming_call(
                user=user, caller=call.initiator, call_id=public_call_id(call), call_type=call.call_type,
                payload=_incoming_payload(call, user),
            )
        call_log("incoming_dispatch", outcome="success")
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        call_log("room_provision_finalize", outcome="failure", reason="unexpected")
        _log_error(f"AOS Call Room Provision Failed: {call_name}")


def _finalize_unanswered(call_name: str, ended_at) -> None:
    if any_invitee_joined(call_name) or not all_invitees_terminal(call_name):
        return
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET status='missed', ended_at=%s, is_active=0, room_cleanup_pending=1,
            rtc_missing_since=NULL, state_version=state_version+1
        WHERE name=%s AND status IN ('initiated','ringing') AND is_active=1
        """,
        (ended_at, call_name),
    )
    if frappe.db._cursor.rowcount:
        frappe.db.sql(
            "UPDATE `tabAOS Call Participant` SET status='left',left_at=COALESCE(left_at,%s) "
            "WHERE `call`=%s AND role='initiator' AND status='joined'",
            (ended_at, call_name),
        )
        call = _reload(call_name)
        _maybe_message(call, "📞 Missed call")
        enqueue_room_cleanup(call.name)


def _mark_participant_missed(row, now) -> bool:
    frappe.db.sql(
        """
        UPDATE `tabAOS Call Participant`
        SET status='missed', responded_at=%s
        WHERE name=%s AND status IN ('invited','ringing')
          AND ring_expires_at IS NOT NULL AND ring_expires_at<=%s
        """,
        (now, row.name, now),
    )
    if frappe.db._cursor.rowcount == 0:
        return False
    frappe.db.sql("UPDATE `tabAOS Call` SET state_version=state_version+1 WHERE name=%s AND is_active=1", (row.call,))
    call = _reload(row.call)
    try:
        publish_participant_missed(call, row.user)
        NotificationService.notify_missed_call(user=row.user, caller=(getattr(row, "added_by", None) or call.initiator), call_id=public_call_id(call))
    except Exception:
        _log_error(f"AOS Missed Call Delivery Failed: {row.call}")
    _finalize_unanswered(row.call, now)
    return True


def _expired_participants(now):
    return frappe.db.sql(
        """
        SELECT p.name,p.`call` AS `call`,p.user,p.added_by,p.ring_expires_at
        FROM `tabAOS Call Participant` p
        INNER JOIN `tabAOS Call` c ON c.name=p.`call`
        WHERE p.status IN ('invited','ringing') AND p.ring_expires_at IS NOT NULL AND p.ring_expires_at<=%s
          AND c.is_active=1 AND c.status IN ('initiated','ringing','ongoing')
        ORDER BY p.ring_expires_at,p.name LIMIT %s
        """,
        (now, MISSED_CALL_BATCH_SIZE),
        as_dict=True,
    )


def _recover_pending_provisioning(now):
    cutoff = add_to_date(now, seconds=-PROVISION_RECOVERY_AGE_SECONDS)
    rows = frappe.get_all(
        "AOS Call",
        filters={"status": "initiated", "is_active": 1, "rtc_provisioned_at": ["is", "not set"], "creation": ["<=", cutoff]},
        pluck="name", order_by="creation asc,name asc", limit=PROVISION_RECOVERY_BATCH_SIZE,
    )
    for name in rows:
        try:
            enqueue_room_provisioning(name)
        except Exception:
            _log_error(f"AOS Call Provision Recovery Enqueue Failed: {name}")


def handle_missed_calls():
    try:
        now = now_datetime()
        _recover_pending_provisioning(now)
        for _ in range(MISSED_CALL_MAX_BATCHES_PER_RUN):
            rows = _expired_participants(now)
            if not rows:
                break
            changed = 0
            for row in rows:
                try:
                    lock_call_participants(row.call)
                    if _mark_participant_missed(row, now):
                        changed += 1
                    frappe.db.commit()
                except Exception:
                    frappe.db.rollback()
                    _log_error(f"AOS Missed Call Processing Failed: {row.call}")
            if len(rows) < MISSED_CALL_BATCH_SIZE or changed == 0:
                break
    except Exception:
        frappe.db.rollback()
        _log_error("AOS Handle Missed Calls Failed")


def cleanup_call_room(call_id: str):
    if not call_id:
        return
    try:
        result = cleanup_room(call_id)
        frappe.db.commit() if result.get("ok") else frappe.db.rollback()
    except Exception:
        frappe.db.rollback()
        _log_error(f"AOS Call Room Cleanup Failed: {call_id}")


def reconcile_call_rooms():
    try:
        call_ids = pending_cleanup_call_ids()
    except Exception:
        frappe.db.rollback(); _log_error("AOS Call Room Cleanup Scan Failed"); return
    for call_id in call_ids:
        try:
            result = cleanup_room(call_id)
            if result.get("ok"):
                frappe.db.commit()
            else:
                frappe.db.rollback(); break
        except Exception:
            frappe.db.rollback(); _log_error(f"AOS Call Room Reconcile Failed: {call_id}")


def reconcile_active_call_state():
    try:
        candidates = active_room_candidates()
    except Exception:
        frappe.db.rollback(); _log_error("AOS Active Call Reconcile Scan Failed"); return
    for candidate in candidates:
        try:
            policy_outcome = reconcile_active_policy(candidate)
            frappe.db.commit()
            if policy_outcome == "failed_policy":
                continue
            room_outcome = reconcile_active_room(candidate)
            frappe.db.commit()
            if room_outcome == "dependency_failure":
                break
        except Exception:
            frappe.db.rollback(); call_log("active_reconcile", outcome="failure", reason="unexpected"); _log_error(f"AOS Active Call Reconcile Failed: {candidate.name}")
