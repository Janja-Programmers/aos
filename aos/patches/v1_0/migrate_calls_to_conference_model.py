"""Move legacy caller/receiver Calls into the canonical participant ledger."""
from __future__ import annotations

import frappe

BATCH = 200
LEGACY_INDEXES = (
    "idx_call_caller_active", "idx_call_receiver_active", "idx_call_caller_history", "idx_call_receiver_history",
    "idx_call_ring_expiry", "idx_call_timeout", "idx_call_provision_recovery",
)
LEGACY_COLUMNS = (
    "naming_series",
    "caller",
    "receiver",
    "incoming_dispatched_at",
    "ring_expires_at",
    "visible_to_caller",
    "visible_to_receiver",
)


def execute() -> None:
    if not frappe.db.table_exists("AOS Call") or not frappe.db.table_exists("AOS Call Participant"):
        return
    if frappe.db.has_column("AOS Call", "caller") and frappe.db.has_column("AOS Call", "receiver"):
        _backfill_calls()
    _drop_legacy_indexes()
    _drop_legacy_columns()


def _backfill_calls() -> None:
    last = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name,caller,receiver,conversation,status,call_type,creation,ringing_at,started_at,ended_at,
                   incoming_dispatched_at,ring_expires_at,visible_to_caller,visible_to_receiver
            FROM `tabAOS Call`
            WHERE name>%s ORDER BY name LIMIT %s
            """,
            (last, BATCH),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            last = row.name
            if not row.caller or not row.receiver:
                continue
            frappe.db.sql(
                """
                UPDATE `tabAOS Call`
                SET initiator=COALESCE(NULLIF(initiator,''),%s), call_mode='direct', max_participants=2, participant_count=2
                WHERE name=%s
                """,
                (row.caller, row.name),
            )
            if not frappe.db.exists("AOS Call Participant", {"call": row.name, "user": row.caller}):
                caller_status = "joined" if row.status in {"initiated", "ringing", "ongoing"} else ("failed" if row.status == "failed" else "left")
                doc = frappe.get_doc({
                    "doctype": "AOS Call Participant", "call": row.name, "user": row.caller,
                    "role": "initiator", "status": caller_status, "added_by": row.caller,
                    "visible": int(row.visible_to_caller if row.visible_to_caller is not None else 1),
                    "invited_at": row.creation, "joined_at": row.creation if caller_status == "joined" else row.started_at,
                    "left_at": row.ended_at if caller_status in {"left", "failed"} else None,
                })
                doc.insert(ignore_permissions=True)
            if not frappe.db.exists("AOS Call Participant", {"call": row.name, "user": row.receiver}):
                status_map = {
                    "initiated": "invited", "ringing": "ringing", "ongoing": "joined", "ended": "left",
                    "rejected": "declined", "missed": "missed", "cancelled": "cancelled", "failed": "failed",
                }
                receiver_status = status_map.get(row.status, "failed")
                doc = frappe.get_doc({
                    "doctype": "AOS Call Participant", "call": row.name, "user": row.receiver,
                    "role": "participant", "status": receiver_status, "added_by": row.caller,
                    "visible": int(row.visible_to_receiver if row.visible_to_receiver is not None else 1),
                    "invited_at": row.creation, "incoming_dispatched_at": row.incoming_dispatched_at,
                    "ring_expires_at": row.ring_expires_at, "ringing_at": row.ringing_at,
                    "joined_at": row.started_at if receiver_status in {"joined", "left"} else None,
                    "responded_at": row.ended_at if receiver_status in {"declined", "missed", "cancelled", "failed"} else None,
                    "left_at": row.ended_at if receiver_status in {"left", "failed"} else None,
                })
                doc.insert(ignore_permissions=True)

            # Pre-conference active rooms were provisioned under the previous
            # two-seat provider contract. They cannot be safely promoted after
            # deployment without disconnecting/recreating RTC state, so close
            # them fail-closed. New post-migration calls use conference-capable
            # rooms even while application membership is still direct.
            if row.status in {"initiated", "ringing", "ongoing"}:
                frappe.db.sql(
                    """
                    UPDATE `tabAOS Call`
                    SET status='failed',is_active=0,ended_at=COALESCE(ended_at,NOW()),
                        duration=CASE WHEN started_at IS NULL THEN 0 ELSE GREATEST(0,TIMESTAMPDIFF(SECOND,started_at,NOW())) END,
                        room_cleanup_pending=1,rtc_missing_since=NULL,state_version=COALESCE(state_version,1)+1
                    WHERE name=%s AND is_active=1
                    """,
                    (row.name,),
                )
                frappe.db.sql(
                    "UPDATE `tabAOS Call Participant` SET status='failed',responded_at=COALESCE(responded_at,NOW()),"
                    "left_at=CASE WHEN status='joined' THEN COALESCE(left_at,NOW()) ELSE left_at END "
                    "WHERE `call`=%s AND status IN ('invited','ringing','joined')",
                    (row.name,),
                )


def _index_exists(name: str) -> bool:
    return bool(frappe.db.sql("SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='tabAOS Call' AND INDEX_NAME=%s LIMIT 1", (name,)))


def _drop_legacy_indexes() -> None:
    for name in LEGACY_INDEXES:
        if _index_exists(name):
            frappe.db.sql_ddl(f"ALTER TABLE `tabAOS Call` DROP INDEX `{name}`")


def _drop_legacy_columns() -> None:
    for column in LEGACY_COLUMNS:
        if frappe.db.has_column("AOS Call", column):
            frappe.db.sql_ddl(f"ALTER TABLE `tabAOS Call` DROP COLUMN `{column}`")
