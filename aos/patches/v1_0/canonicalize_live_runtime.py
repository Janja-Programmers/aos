"""Establish the canonical Live runtime state model on upgraded sites."""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

BATCH = 500


def execute() -> None:
    if not frappe.db.table_exists("AOS Live Stream"):
        return

    now = now_datetime()

    # Fresh production identity contract: client-visible Live IDs are opaque.
    # Historical naming-series rows are retained only as inert history; they
    # are never allowed to remain starting/live after this migration.
    _bounded_update(
        where_sql=(
            "name NOT REGEXP '^LIVE-[0-9a-f]{32}$' "
            "AND status IN ('starting','live')"
        ),
        set_sql=(
            "status='failed', is_active=0, active_host_key=NULL, "
            "ended_at=COALESCE(ended_at,modified,creation), "
            "room_cleanup_pending=CASE WHEN room_name IS NOT NULL AND room_name!='' "
            "THEN 1 ELSE 0 END, last_room_error='legacy_live_id'"
        ),
    )

    # Fail closed for malformed lifecycle values. No migration may manufacture
    # a joinable Live from ambiguous historical state.
    _bounded_update(
        where_sql="status IS NULL OR status='' OR status NOT IN ('starting','live','ended','failed')",
        set_sql=(
            "status='failed', is_active=0, active_host_key=NULL, "
            "ended_at=COALESCE(ended_at,modified,creation), "
            "room_cleanup_pending=CASE WHEN room_name IS NOT NULL AND room_name!='' "
            "THEN 1 ELSE 0 END, last_room_error='invalid_state'"
        ),
    )

    # Live remains the only joinable state. Existing active sessions are
    # preserved, while `starting` merely reserves the host until provisioning.
    _bounded_update(
        where_sql="status='live' AND (COALESCE(is_active,0)!=1 OR started_at IS NULL)",
        set_sql="is_active=1, started_at=COALESCE(started_at,creation), ended_at=NULL",
    )
    _bounded_update(
        where_sql="status='starting' AND (COALESCE(is_active,0)!=0 OR started_at IS NOT NULL OR ended_at IS NOT NULL)",
        set_sql="is_active=0, started_at=NULL, ended_at=NULL, duration_seconds=0",
    )
    _bounded_update(
        where_sql="status IN ('ended','failed') AND (COALESCE(is_active,0)!=0 OR active_host_key IS NOT NULL OR ended_at IS NULL)",
        set_sql=(
            "is_active=0, active_host_key=NULL, "
            "ended_at=COALESCE(ended_at,modified,creation)"
        ),
    )

    # Resolve impossible duplicate host reservations before the unique key is
    # reasserted. Prefer an already-live session, then the newest reservation.
    while True:
        hosts = frappe.db.sql(
            """
            SELECT host_user
            FROM `tabAOS Live Stream`
            WHERE status IN ('starting','live') AND host_user IS NOT NULL AND host_user!=''
            GROUP BY host_user HAVING COUNT(*) > 1
            ORDER BY host_user
            LIMIT 100
            """,
            pluck=True,
        )
        if not hosts:
            break
        for host in hosts:
            rows = frappe.db.sql(
                """
                SELECT name, status
                FROM `tabAOS Live Stream`
                WHERE host_user=%s AND status IN ('starting','live')
                ORDER BY (status='live') DESC, started_at DESC, creation DESC, name DESC
                """,
                (host,),
                as_dict=True,
            )
            stale = tuple(str(row.name) for row in rows[1:])
            if stale:
                frappe.db.sql(
                    """
                    UPDATE `tabAOS Live Stream`
                    SET status='failed', is_active=0, active_host_key=NULL,
                        ended_at=COALESCE(ended_at,%(now)s),
                        room_cleanup_pending=CASE WHEN room_name IS NOT NULL AND room_name!='' THEN 1 ELSE 0 END,
                        last_room_error='duplicate_host_reservation'
                    WHERE name IN %(names)s
                    """,
                    {"names": stale, "now": now},
                )

    _bounded_update(
        where_sql="status IN ('starting','live') AND (active_host_key IS NULL OR active_host_key!=host_user)",
        set_sql="active_host_key=host_user",
    )
    _bounded_update(
        where_sql="status NOT IN ('starting','live') AND active_host_key IS NOT NULL",
        set_sql="active_host_key=NULL",
    )
    _bounded_update(
        where_sql="room_provision_attempts IS NULL OR room_provision_attempts < 0",
        set_sql="room_provision_attempts=0",
    )

    # Webhook ordering fields are observations, never lifecycle authorities.
    # Existing active views start with a clean watermark and advance only from
    # verified callbacks after this patch.
    if frappe.db.table_exists("AOS Live Stream View"):
        _bounded_update_table(
            table="AOS Live Stream View",
            where_sql="last_livekit_event_at IS NOT NULL AND joined_at IS NOT NULL AND last_livekit_event_at < joined_at",
            set_sql="last_livekit_event_at=NULL",
        )


def _bounded_update(*, where_sql: str, set_sql: str) -> None:
    _bounded_update_table(
        table="AOS Live Stream",
        where_sql=where_sql,
        set_sql=set_sql,
    )


def _bounded_update_table(*, table: str, where_sql: str, set_sql: str) -> None:
    while True:
        names = frappe.db.sql(
            f"SELECT name FROM `tab{table}` WHERE {where_sql} ORDER BY name LIMIT %s",
            (BATCH,),
            pluck=True,
        )
        if not names:
            return
        frappe.db.sql(
            f"UPDATE `tab{table}` SET {set_sql} WHERE name IN %(names)s",
            {"names": tuple(names)},
        )
