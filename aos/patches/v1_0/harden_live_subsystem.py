"""Bounded, idempotent data reconciliation for the AOS Live subsystem.

The patch owns no transaction, performs no external LiveKit calls, and may be
rerun safely. Schema indexes are installed by the following DDL-only patch.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.services.live.livekit import participant_identity
from aos.services.live_analytics_service import LiveAnalyticsService

BATCH = 250


def _bounded_update(
    doctype: str,
    *,
    where_sql: str,
    set_sql: str,
    params: dict | None = None,
) -> int:
    """Apply a deterministic idempotent repair in bounded primary-key batches."""
    total = 0
    table = f"tab{doctype}"
    base = dict(params or {})
    while True:
        query_params = dict(base)
        query_params["batch"] = BATCH
        names = frappe.db.sql(
            f"SELECT name FROM `{table}` WHERE {where_sql} ORDER BY name LIMIT %(batch)s",
            query_params,
            pluck=True,
        )
        if not names:
            return total
        update_params = dict(base)
        update_params["names"] = tuple(names)
        frappe.db.sql(
            f"UPDATE `{table}` SET {set_sql} WHERE name IN %(names)s",
            update_params,
        )
        total += len(names)


def execute() -> None:
    if not frappe.db.table_exists("AOS Live Stream"):
        return
    _normalize_streams()
    _normalize_views()
    _normalize_cohosts()
    _normalize_messages()
    _reconcile_metrics()


def _normalize_streams() -> None:
    now = now_datetime()
    # Unknown legacy lifecycle states fail closed and can never become active.
    while True:
        rows = frappe.db.sql(
            """
            SELECT name FROM `tabAOS Live Stream`
            WHERE status IS NULL OR status = '' OR status NOT IN ('scheduled', 'live', 'ended')
            ORDER BY name LIMIT %s
            """,
            (BATCH,),
            pluck=True,
        )
        if not rows:
            break
        frappe.db.sql(
            """
            UPDATE `tabAOS Live Stream`
            SET status='ended', is_active=0, active_host_key=NULL,
                ended_at=COALESCE(ended_at, modified, creation), room_cleanup_pending=1
            WHERE name IN %(names)s
            """,
            {"names": tuple(rows)},
        )

    _bounded_update(
        "AOS Live Stream",
        where_sql="room_name IS NULL OR room_name = ''",
        set_sql="room_name = CONCAT('live:', name)",
    )
    _normalize_duplicate_room_names()
    _bounded_update(
        "AOS Live Stream",
        where_sql="status='live' AND (started_at IS NULL OR COALESCE(is_active,0)!=1)",
        set_sql="started_at=COALESCE(started_at,creation), is_active=1",
    )
    _bounded_update(
        "AOS Live Stream",
        where_sql=(
            "status IN ('scheduled','ended') AND (COALESCE(is_active,0)!=0 "
            "OR active_host_key IS NOT NULL "
            "OR (status='ended' AND ended_at IS NULL) "
            "OR (status='ended' AND room_name IS NOT NULL AND room_name!='' "
            "AND COALESCE(room_cleanup_pending,0)!=1))"
        ),
        set_sql=(
            "is_active=0, active_host_key=NULL, "
            "ended_at=CASE WHEN status='ended' THEN COALESCE(ended_at,modified,creation) ELSE ended_at END, "
            "room_cleanup_pending=CASE WHEN status='ended' AND room_name IS NOT NULL "
            "AND room_name!='' THEN 1 ELSE room_cleanup_pending END"
        ),
    )

    # Active hosts must still be enabled, active and non-deleted.
    while True:
        rows = frappe.db.sql(
            """
            SELECT l.name
            FROM `tabAOS Live Stream` l
            LEFT JOIN `tabUser` u ON u.name=l.host_user
            LEFT JOIN `tabAOS Profile` p ON p.user=l.host_user
            WHERE l.status='live' AND l.is_active=1
              AND (u.name IS NULL OR COALESCE(u.enabled,0)=0 OR p.name IS NULL
                   OR COALESCE(p.is_deleted,0)=1
                   OR COALESCE(NULLIF(p.account_status,''),'Active')!='Active')
            ORDER BY l.name LIMIT %s
            """,
            (BATCH,),
            pluck=True,
        )
        if not rows:
            break
        frappe.db.sql(
            """
            UPDATE `tabAOS Live Stream`
            SET status='ended', is_active=0, active_host_key=NULL,
                ended_at=COALESCE(ended_at,%(now)s), room_cleanup_pending=1
            WHERE name IN %(names)s
            """,
            {"names": tuple(rows), "now": now},
        )

    # Keep the newest active stream for a host; safely terminate stale siblings.
    while True:
        groups = frappe.db.sql(
            """
            SELECT host_user
            FROM `tabAOS Live Stream`
            WHERE status='live' AND is_active=1 AND host_user IS NOT NULL AND host_user!=''
            GROUP BY host_user HAVING COUNT(*) > 1
            ORDER BY host_user LIMIT 100
            """,
            pluck=True,
        )
        if not groups:
            break
        for host_user in groups:
            names = frappe.db.sql(
                """
                SELECT name FROM `tabAOS Live Stream`
                WHERE host_user=%s AND status='live' AND is_active=1
                ORDER BY started_at DESC, creation DESC, name DESC
                """,
                (host_user,),
                pluck=True,
            )
            stale = tuple(names[1:])
            if stale:
                frappe.db.sql(
                    """
                    UPDATE `tabAOS Live Stream`
                    SET status='ended', is_active=0, active_host_key=NULL,
                        ended_at=COALESCE(ended_at,%(now)s), room_cleanup_pending=1
                    WHERE name IN %(names)s
                    """,
                    {"names": stale, "now": now},
                )

    _bounded_update(
        "AOS Live Stream",
        where_sql="active_host_key IS NOT NULL AND NOT (status='live' AND is_active=1)",
        set_sql="active_host_key=NULL",
    )
    _bounded_update(
        "AOS Live Stream",
        where_sql=(
            "status='live' AND is_active=1 AND "
            "(active_host_key IS NULL OR active_host_key!=host_user)"
        ),
        set_sql="active_host_key=host_user",
    )
    _bounded_update(
        "AOS Live Stream",
        where_sql=(
            "viewer_count IS NULL OR viewer_count<0 OR total_views IS NULL OR total_views<0 "
            "OR total_joins IS NULL OR total_joins<0 OR unique_viewers IS NULL OR unique_viewers<0 "
            "OR peak_viewers IS NULL OR peak_viewers<0 OR comment_count IS NULL OR comment_count<0 "
            "OR reaction_count IS NULL OR reaction_count<0 "
            "OR total_watch_time_seconds IS NULL OR total_watch_time_seconds<0"
        ),
        set_sql=(
            "viewer_count=GREATEST(COALESCE(viewer_count,0),0), "
            "total_views=GREATEST(COALESCE(total_views,0),0), "
            "total_joins=GREATEST(COALESCE(total_joins,0),0), "
            "unique_viewers=GREATEST(COALESCE(unique_viewers,0),0), "
            "peak_viewers=GREATEST(COALESCE(peak_viewers,0),0), "
            "comment_count=GREATEST(COALESCE(comment_count,0),0), "
            "reaction_count=GREATEST(COALESCE(reaction_count,0),0), "
            "total_watch_time_seconds=GREATEST(COALESCE(total_watch_time_seconds,0),0)"
        ),
    )


def _room_name_available(candidate: str, live_id: str) -> bool:
    return not bool(
        frappe.db.get_value(
            "AOS Live Stream",
            {"room_name": candidate, "name": ["!=", live_id]},
            "name",
        )
    )


def _replacement_room_name(live_id: str) -> str:
    candidates = (
        f"live:{live_id}",
        f"live:{live_id}:reconciled",
        f"live-reconciled:{live_id}",
    )
    for candidate in candidates:
        if _room_name_available(candidate, live_id):
            return candidate
    frappe.throw(f"Unable to allocate a unique Live room name for {live_id}")


def _normalize_duplicate_room_names() -> None:
    """Keep one legitimate room owner and deterministically re-key siblings."""
    while True:
        duplicates = frappe.db.sql(
            """
            SELECT room_name
            FROM `tabAOS Live Stream`
            WHERE room_name IS NOT NULL AND room_name != ''
            GROUP BY room_name HAVING COUNT(*) > 1
            ORDER BY room_name
            LIMIT 100
            """,
            pluck=True,
        )
        if not duplicates:
            return
        for room_name in duplicates:
            rows = frappe.db.sql(
                """
                SELECT name, status, is_active
                FROM `tabAOS Live Stream`
                WHERE room_name = %s
                ORDER BY (status='live' AND is_active=1) DESC,
                         started_at DESC, creation DESC, name DESC
                LIMIT %s
                """,
                (room_name, BATCH),
                as_dict=True,
            )
            for row in rows[1:]:
                replacement = _replacement_room_name(str(row.name))
                frappe.db.set_value(
                    "AOS Live Stream",
                    row.name,
                    {
                        "room_name": replacement,
                        "last_reconciled_at": None,
                        "room_cleanup_pending": int(str(row.status) == "ended"),
                    },
                    update_modified=False,
                )


def _close_view_names(names: tuple[str, ...], now) -> None:
    if not names:
        return
    frappe.db.sql(
        """
        UPDATE `tabAOS Live Stream View`
        SET is_active=0, active_identity_key=NULL,
            left_at=COALESCE(left_at,%(now)s), last_seen_at=COALESCE(last_seen_at,%(now)s),
            watch_duration_seconds=GREATEST(
                COALESCE(watch_duration_seconds, 0),
                COALESCE(TIMESTAMPDIFF(SECOND, joined_at, %(now)s), 0),
                0
            )
        WHERE name IN %(names)s
        """,
        {"names": names, "now": now},
    )


def _normalize_views() -> None:
    if not frappe.db.table_exists("AOS Live Stream View"):
        return
    now = now_datetime()
    # Ended/missing lives and unavailable authenticated accounts cannot retain presence.
    while True:
        rows = frappe.db.sql(
            """
            SELECT v.name
            FROM `tabAOS Live Stream View` v
            LEFT JOIN `tabAOS Live Stream` l ON l.name=v.live_stream
            LEFT JOIN `tabUser` u ON u.name=v.user
            LEFT JOIN `tabAOS Profile` p ON p.user=v.user
            WHERE v.is_active=1 AND (
                l.name IS NULL OR l.status!='live' OR COALESCE(l.is_active,0)=0
                OR (v.user IS NOT NULL AND v.user!='' AND (
                    u.name IS NULL OR COALESCE(u.enabled,0)=0 OR p.name IS NULL
                    OR COALESCE(p.is_deleted,0)=1
                    OR COALESCE(NULLIF(p.account_status,''),'Active')!='Active'
                ))
            )
            ORDER BY v.name LIMIT %s
            """,
            (BATCH,),
            pluck=True,
        )
        if not rows:
            break
        _close_view_names(tuple(rows), now)

    while True:
        groups = frappe.db.sql(
            """
            SELECT live_stream, session_id
            FROM `tabAOS Live Stream View`
            WHERE is_active=1 AND session_id IS NOT NULL AND session_id!=''
            GROUP BY live_stream, session_id HAVING COUNT(*) > 1
            ORDER BY live_stream, session_id LIMIT 100
            """,
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            names = frappe.db.sql(
                """
                SELECT name FROM `tabAOS Live Stream View`
                WHERE live_stream=%s AND session_id=%s AND is_active=1
                ORDER BY last_seen_at DESC, creation DESC, name DESC
                """,
                (group.live_stream, group.session_id),
                pluck=True,
            )
            _close_view_names(tuple(names[1:]), now)

    _bounded_update(
        "AOS Live Stream View",
        where_sql="active_identity_key IS NOT NULL AND COALESCE(is_active,0)!=1",
        set_sql="active_identity_key=NULL",
    )
    _bounded_update(
        "AOS Live Stream View",
        where_sql=(
            "is_active=1 AND session_id IS NOT NULL AND session_id!='' AND "
            "(active_identity_key IS NULL OR active_identity_key!=session_id)"
        ),
        set_sql="active_identity_key=session_id",
    )

    start = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, live_stream, user, session_id
            FROM `tabAOS Live Stream View`
            WHERE name > %s
            ORDER BY name LIMIT %s
            """,
            (start, BATCH),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            try:
                identity = participant_identity(
                    live_id=row.live_stream,
                    role="viewer",
                    user=row.user or None,
                    session_id=row.session_id,
                )
            except Exception:
                identity = None
            frappe.db.set_value(
                "AOS Live Stream View",
                row.name,
                "livekit_identity",
                identity,
                update_modified=False,
            )
        start = rows[-1].name


def _normalize_cohosts() -> None:
    if not frappe.db.table_exists("AOS Live CoHost"):
        return
    now = now_datetime()
    _bounded_update(
        "AOS Live CoHost",
        where_sql=(
            "status IS NULL OR status='' OR status NOT IN "
            "('pending','accepted','rejected','cancelled','active','ended','expired')"
        ),
        set_sql=(
            "status='cancelled', is_active=0, active_workflow_key=NULL, "
            "responded_at=COALESCE(responded_at,%(now)s), "
            "response_reason='Invalid legacy status.'"
        ),
        params={"now": now},
    )
    while True:
        rows = frappe.db.sql(
            """
            SELECT c.name
            FROM `tabAOS Live CoHost` c
            INNER JOIN `tabAOS Live Stream` l ON l.name=c.live_stream
            WHERE c.status IN ('pending','accepted','active')
              AND (l.status!='live' OR COALESCE(l.is_active,0)=0 OR c.user=l.host_user)
            ORDER BY c.name LIMIT %s
            """,
            (BATCH,),
            pluck=True,
        )
        if not rows:
            break
        frappe.db.sql(
            """
            UPDATE `tabAOS Live CoHost`
            SET ended_at=CASE WHEN status='active' THEN COALESCE(ended_at,%(now)s) ELSE ended_at END,
                status=CASE WHEN status='active' THEN 'ended' ELSE 'cancelled' END,
                is_active=0, active_workflow_key=NULL,
                response_reason=COALESCE(NULLIF(response_reason,''),'Parent live is not active.')
            WHERE name IN %(names)s
            """,
            {"names": tuple(rows), "now": now},
        )

    while True:
        groups = frappe.db.sql(
            """
            SELECT live_stream, user
            FROM `tabAOS Live CoHost`
            WHERE status IN ('pending','accepted','active')
            GROUP BY live_stream, user HAVING COUNT(*) > 1
            ORDER BY live_stream, user LIMIT 100
            """,
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            rows = frappe.db.sql(
                """
                SELECT name, status FROM `tabAOS Live CoHost`
                WHERE live_stream=%s AND user=%s AND status IN ('pending','accepted','active')
                ORDER BY FIELD(status,'active','accepted','pending'), requested_at ASC, creation ASC, name ASC
                """,
                (group.live_stream, group.user),
                as_dict=True,
            )
            stale = tuple(row.name for row in rows[1:])
            if stale:
                frappe.db.sql(
                    """
                    UPDATE `tabAOS Live CoHost`
                    SET status='cancelled', is_active=0, active_workflow_key=NULL,
                        responded_at=COALESCE(responded_at,%(now)s),
                        response_reason='Duplicate co-host workflow reconciled.'
                    WHERE name IN %(names)s
                    """,
                    {"names": stale, "now": now},
                )

    _bounded_update(
        "AOS Live CoHost",
        where_sql=(
            "active_workflow_key IS NOT NULL AND "
            "status NOT IN ('pending','accepted','active')"
        ),
        set_sql="active_workflow_key=NULL",
    )
    _bounded_update(
        "AOS Live CoHost",
        where_sql=(
            "status IN ('pending','accepted','active') AND live_stream IS NOT NULL "
            "AND live_stream!='' AND user IS NOT NULL AND user!='' AND "
            "(active_workflow_key IS NULL OR active_workflow_key!=CONCAT(live_stream,'|',user))"
        ),
        set_sql="active_workflow_key=CONCAT(live_stream,'|',user)",
    )
    start = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, live_stream, user, session_id, status
            FROM `tabAOS Live CoHost`
            WHERE name > %s ORDER BY name LIMIT %s
            """,
            (start, BATCH),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            identity = None
            if row.status in {"accepted", "active"} and row.user and row.session_id:
                try:
                    identity = participant_identity(
                        live_id=row.live_stream,
                        role="cohost",
                        user=row.user,
                        session_id=row.session_id,
                    )
                except Exception:
                    identity = None
            frappe.db.set_value(
                "AOS Live CoHost",
                row.name,
                "livekit_identity",
                identity,
                update_modified=False,
            )
        start = rows[-1].name


def _normalize_messages() -> None:
    if not frappe.db.table_exists("AOS Live Message"):
        return
    _bounded_update(
        "AOS Live Message",
        where_sql=(
            "message_kind='comment' AND status!='deleted' AND ("
            "content IS NULL OR TRIM(content)='' OR user IS NULL OR user='')"
        ),
        set_sql="status='deleted', active_idempotency_key=NULL",
    )
    while True:
        groups = frappe.db.sql(
            """
            SELECT live_stream, user, idempotency_key
            FROM `tabAOS Live Message`
            WHERE message_kind='comment' AND status='active'
              AND idempotency_key IS NOT NULL AND idempotency_key!=''
            GROUP BY live_stream, user, idempotency_key HAVING COUNT(*) > 1
            ORDER BY live_stream, user, idempotency_key LIMIT 100
            """,
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            names = frappe.db.sql(
                """
                SELECT name FROM `tabAOS Live Message`
                WHERE live_stream=%s AND user=%s AND idempotency_key=%s
                  AND message_kind='comment' AND status='active'
                ORDER BY creation ASC, name ASC
                """,
                (group.live_stream, group.user, group.idempotency_key),
                pluck=True,
            )
            if len(names) > 1:
                frappe.db.sql(
                    """
                    UPDATE `tabAOS Live Message`
                    SET status='deleted', active_idempotency_key=NULL
                    WHERE name IN %(names)s
                    """,
                    {"names": tuple(names[1:])},
                )
    _bounded_update(
        "AOS Live Message",
        where_sql=(
            "active_idempotency_key IS NOT NULL AND "
            "NOT (message_kind='comment' AND status='active' "
            "AND idempotency_key IS NOT NULL AND idempotency_key!='')"
        ),
        set_sql="active_idempotency_key=NULL",
    )
    _bounded_update(
        "AOS Live Message",
        where_sql=(
            "message_kind='comment' AND status='active' AND user IS NOT NULL AND user!='' "
            "AND idempotency_key IS NOT NULL AND idempotency_key!='' "
            "AND (active_idempotency_key IS NULL OR "
            "active_idempotency_key!=CONCAT(live_stream,'|',user,'|',idempotency_key))"
        ),
        set_sql="active_idempotency_key=CONCAT(live_stream,'|',user,'|',idempotency_key)",
    )


def _reconcile_metrics() -> None:
    start = ""
    while True:
        names = frappe.db.sql(
            """
            SELECT name FROM `tabAOS Live Stream`
            WHERE name > %s ORDER BY name LIMIT %s
            """,
            (start, BATCH),
            pluck=True,
        )
        if not names:
            break
        for live_id in names:
            LiveAnalyticsService.sync_live_metrics(live_id=live_id)
        start = names[-1]
