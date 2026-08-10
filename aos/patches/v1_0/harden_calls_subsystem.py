"""Bounded, idempotent data reconciliation for the AOS Calls subsystem.

The patch owns no transaction and performs no LiveKit, realtime, notification,
or other external side effects. It repairs only objectively inconsistent legacy
rows so the following Calls indexes can be installed safely.
"""

from __future__ import annotations

import hashlib

import frappe
from frappe.utils import now_datetime

BATCH = 100
ACTIVE = ("initiated", "ringing", "ongoing")
TERMINAL = ("ended", "missed", "rejected", "failed", "cancelled")
VALID = ACTIVE + TERMINAL


def execute() -> None:
    if not frappe.db.table_exists("AOS Call"):
        return
    _normalize_unknown_states()
    _normalize_self_calls()
    # Align state/active flags before conflict repair. Otherwise a legacy
    # active-status row with is_active=0 could be revived after overlap checks.
    _normalize_active_flags()
    _normalize_missing_room_names()
    _resolve_duplicate_room_names()
    _resolve_overlapping_active_calls()
    _normalize_terminal_metadata()


def _bounded_names(where_sql: str, params: dict | None = None) -> list[str]:
    values = dict(params or {})
    values["batch"] = BATCH
    return frappe.db.sql(
        f"SELECT name FROM `tabAOS Call` WHERE {where_sql} ORDER BY creation, name LIMIT %(batch)s",
        values,
        pluck=True,
    )


def _fail_rows(names: list[str] | tuple[str, ...], *, cleanup_pending: int = 1) -> None:
    if not names:
        return
    now = now_datetime()
    frappe.db.sql(
        """
        UPDATE `tabAOS Call`
        SET status='failed', is_active=0,
            ended_at=COALESCE(ended_at,%(now)s),
            duration=CASE
                WHEN started_at IS NULL THEN COALESCE(duration,0)
                ELSE GREATEST(0,TIMESTAMPDIFF(SECOND,started_at,COALESCE(ended_at,%(now)s)))
            END,
            room_cleanup_pending=%(cleanup_pending)s,
            rtc_missing_since=NULL
        WHERE name IN %(names)s
        """,
        {
            "names": tuple(names),
            "now": now,
            "cleanup_pending": int(bool(cleanup_pending)),
        },
    )


def _normalize_unknown_states() -> None:
    while True:
        names = _bounded_names(
            "status IS NULL OR status='' OR status NOT IN %(valid)s",
            {"valid": VALID},
        )
        if not names:
            return
        _fail_rows(names)


def _normalize_self_calls() -> None:
    while True:
        names = _bounded_names(
            "caller IS NOT NULL AND caller!='' AND caller=receiver "
            "AND status IN %(active)s",
            {"active": ACTIVE},
        )
        if not names:
            return
        _fail_rows(names)


def _recovered_room_name(call_id: str) -> str:
    digest = hashlib.sha256(str(call_id).encode("utf-8")).hexdigest()[:32]
    return f"call:recovered:{digest}"


def _normalize_missing_room_names() -> None:
    while True:
        names = _bounded_names("room_name IS NULL OR room_name=''")
        if not names:
            return
        for call_id in names:
            frappe.db.sql(
                "UPDATE `tabAOS Call` SET room_name=%s WHERE name=%s AND (room_name IS NULL OR room_name='')",
                (_recovered_room_name(call_id), call_id),
            )


def _resolve_duplicate_room_names() -> None:
    """Keep one canonical owner of a legacy duplicate room name.

    Active duplicates cannot safely share RTC state. Non-kept active rows are
    terminalized without scheduling deletion of the shared room. Every non-kept
    row receives a deterministic inert room name so a unique index can follow.
    """
    while True:
        duplicate_rooms = frappe.db.sql(
            """
            SELECT room_name
            FROM `tabAOS Call`
            WHERE room_name IS NOT NULL AND room_name!=''
            GROUP BY room_name HAVING COUNT(*)>1
            ORDER BY room_name LIMIT %(batch)s
            """,
            {"batch": BATCH},
            pluck=True,
        )
        if not duplicate_rooms:
            return

        for room_name in duplicate_rooms:
            rows = frappe.db.sql(
                """
                SELECT name,status,is_active
                FROM `tabAOS Call`
                WHERE room_name=%s
                ORDER BY (status IN ('initiated','ringing','ongoing') AND COALESCE(is_active,0)=1) DESC,
                         creation DESC, name DESC
                """,
                (room_name,),
                as_dict=True,
            )
            for row in rows[1:]:
                new_room = _recovered_room_name(row.name)
                frappe.db.sql(
                    "UPDATE `tabAOS Call` SET room_name=%s WHERE name=%s AND room_name=%s",
                    (new_room, row.name, room_name),
                )
                if row.status in ACTIVE and int(row.is_active or 0):
                    _fail_rows([row.name], cleanup_pending=0)


def _resolve_overlapping_active_calls() -> None:
    """Keep the newest call whenever legacy active rows share a participant."""
    while True:
        stale = frappe.db.sql(
            """
            SELECT c.name
            FROM `tabAOS Call` c
            WHERE c.status IN ('initiated','ringing','ongoing')
              AND COALESCE(c.is_active,0)=1
              AND EXISTS (
                SELECT 1 FROM `tabAOS Call` newer
                WHERE newer.name!=c.name
                  AND newer.status IN ('initiated','ringing','ongoing')
                  AND COALESCE(newer.is_active,0)=1
                  AND (
                    newer.caller IN (c.caller,c.receiver)
                    OR newer.receiver IN (c.caller,c.receiver)
                  )
                  AND (
                    newer.creation>c.creation
                    OR (newer.creation=c.creation AND newer.name>c.name)
                  )
              )
            ORDER BY c.creation,c.name
            LIMIT %(batch)s
            """,
            {"batch": BATCH},
            pluck=True,
        )
        if not stale:
            return
        _fail_rows(stale)


def _normalize_active_flags() -> None:
    while True:
        names = _bounded_names(
            "status IN %(active)s AND COALESCE(is_active,0)!=1",
            {"active": ACTIVE},
        )
        if not names:
            break
        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET is_active=1, room_cleanup_pending=0
            WHERE name IN %(names)s AND status IN %(active)s
            """,
            {"names": tuple(names), "active": ACTIVE},
        )

    while True:
        names = _bounded_names(
            "status IN %(terminal)s AND COALESCE(is_active,0)!=0",
            {"terminal": TERMINAL},
        )
        if not names:
            return
        frappe.db.sql(
            "UPDATE `tabAOS Call` SET is_active=0, rtc_missing_since=NULL "
            "WHERE name IN %(names)s AND status IN %(terminal)s",
            {"names": tuple(names), "terminal": TERMINAL},
        )


def _normalize_terminal_metadata() -> None:
    now = now_datetime()
    while True:
        names = _bounded_names(
            "status IN %(terminal)s AND (ended_at IS NULL OR duration IS NULL OR duration<0)",
            {"terminal": TERMINAL},
        )
        if not names:
            return
        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET ended_at=COALESCE(ended_at,modified,creation,%(now)s),
                duration=CASE
                    WHEN started_at IS NULL THEN GREATEST(COALESCE(duration,0),0)
                    ELSE GREATEST(0,TIMESTAMPDIFF(SECOND,started_at,COALESCE(ended_at,modified,creation,%(now)s)))
                END,
                rtc_missing_since=NULL
            WHERE name IN %(names)s
            """,
            {"names": tuple(names), "now": now},
        )
