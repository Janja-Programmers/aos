"""Bounded, idempotent production hardening for Shorts persistence.

The patch deliberately owns no transaction and is safe to rerun. Duplicate
interaction rows are reconciled before database uniqueness is installed.
"""
from __future__ import annotations

import hashlib

import frappe
from frappe.utils import add_to_date, now_datetime

_BATCH = 250


def execute() -> None:
    _dedupe_exact("AOS Short Like", ("short", "user"))
    _dedupe_exact("AOS Short Save", ("short", "user"))
    _dedupe_exact("AOS Short Comment Like", ("comment", "user"))
    _dedupe_exact("AOS Short Sound", ("short",))
    _dedupe_views()
    _dedupe_daily_metrics()
    _normalize_reports()
    _normalize_reposts()
    _normalize_processing_jobs()
    _backfill_event_keys()
    _normalize_short_states()
    _reconcile_short_counters()


def _table(doctype: str) -> str:
    return f"tab{doctype}"


def _dedupe_exact(doctype: str, fields: tuple[str, ...]) -> None:
    if not frappe.db.table_exists(doctype):
        return
    select = ", ".join(f"`{field}`" for field in fields)
    group = ", ".join(f"`{field}`" for field in fields)
    while True:
        groups = frappe.db.sql(
            f"""SELECT {select} FROM `{_table(doctype)}`
                WHERE {fields[0]} IS NOT NULL AND {fields[0]} != ''
                GROUP BY {group} HAVING COUNT(*) > 1
                ORDER BY {group} LIMIT 100""",
            as_dict=True,
        )
        if not groups:
            return
        for values in groups:
            clauses = " AND ".join(f"`{field}` = %s" for field in fields)
            args = tuple(values.get(field) for field in fields)
            keep = frappe.db.sql(
                f"SELECT name FROM `{_table(doctype)}` WHERE {clauses} "
                "ORDER BY creation ASC, name ASC LIMIT 1",
                args,
                pluck=True,
            )
            if not keep:
                continue
            while True:
                stale = frappe.db.sql(
                    f"SELECT name FROM `{_table(doctype)}` WHERE {clauses} AND name != %s "
                    "ORDER BY creation ASC, name ASC LIMIT %s",
                    (*args, keep[0], _BATCH),
                    pluck=True,
                )
                if not stale:
                    break
                frappe.db.sql(
                    f"DELETE FROM `{_table(doctype)}` WHERE name IN %(names)s",
                    {"names": tuple(stale)},
                )


def _dedupe_views() -> None:
    if not frappe.db.table_exists("AOS Short View"):
        return
    while True:
        groups = frappe.db.sql(
            """SELECT short, view_date, identity_key
               FROM `tabAOS Short View`
               WHERE identity_key IS NOT NULL AND identity_key != ''
               GROUP BY short, view_date, identity_key HAVING COUNT(*) > 1
               ORDER BY short, view_date, identity_key LIMIT 100""",
            as_dict=True,
        )
        if not groups:
            return
        for group in groups:
            rows = frappe.db.sql(
                """SELECT name, watch_ms, qualified, last_seen_at
                   FROM `tabAOS Short View`
                   WHERE short=%s AND view_date=%s AND identity_key=%s
                   ORDER BY creation ASC, name ASC""",
                (group.short, group.view_date, group.identity_key),
                as_dict=True,
            )
            if len(rows) < 2:
                continue
            keep, stale = rows[0], rows[1:]
            frappe.db.set_value(
                "AOS Short View", keep.name,
                {
                    "watch_ms": max(int(row.watch_ms or 0) for row in rows),
                    "qualified": max(int(row.qualified or 0) for row in rows),
                    "last_seen_at": max((row.last_seen_at for row in rows if row.last_seen_at), default=None),
                },
                update_modified=False,
            )
            frappe.db.sql(
                "DELETE FROM `tabAOS Short View` WHERE name IN %(names)s",
                {"names": tuple(row.name for row in stale)},
            )


def _dedupe_daily_metrics() -> None:
    if not frappe.db.table_exists("AOS Short Metrics Daily"):
        return
    additive = ("impressions", "views", "watch_time_ms", "likes", "comments", "shares", "saves", "downloads", "reposts")
    while True:
        groups = frappe.db.sql(
            """SELECT short, date FROM `tabAOS Short Metrics Daily`
               GROUP BY short, date HAVING COUNT(*) > 1
               ORDER BY short, date LIMIT 100""",
            as_dict=True,
        )
        if not groups:
            return
        for group in groups:
            rows = frappe.db.sql(
                "SELECT * FROM `tabAOS Short Metrics Daily` WHERE short=%s AND date=%s ORDER BY creation ASC, name ASC",
                (group.short, group.date), as_dict=True,
            )
            keep, stale = rows[0], rows[1:]
            values = {field: sum(max(0, int(row.get(field) or 0)) for row in rows) for field in additive}
            values["avg_watch_time_ms"] = int(values["watch_time_ms"] / values["views"]) if values["views"] else 0
            values["completion_rate"] = max(float(row.get("completion_rate") or 0) for row in rows)
            frappe.db.set_value("AOS Short Metrics Daily", keep.name, values, update_modified=False)
            frappe.db.sql(
                "DELETE FROM `tabAOS Short Metrics Daily` WHERE name IN %(names)s",
                {"names": tuple(row.name for row in stale)},
            )


def _digest(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(part or "") for part in parts).encode()).hexdigest()


def _normalize_reports() -> None:
    if not frappe.db.table_exists("AOS Short Report"):
        return
    frappe.db.sql("UPDATE `tabAOS Short Report` SET active_key=NULL WHERE status != 'Reviewing' OR status IS NULL")
    while True:
        groups = frappe.db.sql(
            """SELECT short, reported_by FROM `tabAOS Short Report`
               WHERE status='Reviewing' GROUP BY short, reported_by HAVING COUNT(*) > 1
               ORDER BY short, reported_by LIMIT 100""", as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            names = frappe.db.sql(
                """SELECT name FROM `tabAOS Short Report`
                   WHERE short=%s AND reported_by=%s AND status='Reviewing'
                   ORDER BY creation ASC, name ASC""",
                (group.short, group.reported_by), pluck=True,
            )
            if len(names) > 1:
                frappe.db.sql(
                    "UPDATE `tabAOS Short Report` SET status='Rejected', active_key=NULL WHERE name IN %(names)s",
                    {"names": tuple(names[1:])},
                )
    _backfill_key("AOS Short Report", "status='Reviewing'", "active_key", ("short", "reported_by"))


def _normalize_reposts() -> None:
    if not frappe.db.table_exists("AOS Short Repost"):
        return
    frappe.db.sql("UPDATE `tabAOS Short Repost` SET active_key=NULL WHERE status != 'active' OR status IS NULL")
    while True:
        groups = frappe.db.sql(
            """SELECT short, user FROM `tabAOS Short Repost`
               WHERE status='active' GROUP BY short, user HAVING COUNT(*) > 1
               ORDER BY short, user LIMIT 100""", as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            names = frappe.db.sql(
                """SELECT name FROM `tabAOS Short Repost`
                   WHERE short=%s AND user=%s AND status='active'
                   ORDER BY creation ASC, name ASC""",
                (group.short, group.user), pluck=True,
            )
            if len(names) > 1:
                frappe.db.sql(
                    "UPDATE `tabAOS Short Repost` SET status='deleted', active_key=NULL WHERE name IN %(names)s",
                    {"names": tuple(names[1:])},
                )
    _backfill_key("AOS Short Repost", "status='active'", "active_key", ("short", "user"))


def _normalize_processing_jobs() -> None:
    if not frappe.db.table_exists("AOS Video Processing Job"):
        return
    active = ("Queued", "Dispatching", "Processing")
    frappe.db.sql(
        "UPDATE `tabAOS Video Processing Job` SET active_key=NULL WHERE status NOT IN %(statuses)s OR status IS NULL",
        {"statuses": active},
    )
    while True:
        groups = frappe.db.sql(
            """SELECT short FROM `tabAOS Video Processing Job`
               WHERE status IN ('Queued','Dispatching','Processing')
               GROUP BY short HAVING COUNT(*) > 1 ORDER BY short LIMIT 100""", as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            rows = frappe.db.sql(
                """SELECT name FROM `tabAOS Video Processing Job`
                   WHERE short=%s AND status IN ('Queued','Dispatching','Processing')
                   ORDER BY generation DESC, creation DESC, name DESC""",
                (group.short,), as_dict=True,
            )
            if len(rows) > 1:
                frappe.db.sql(
                    "UPDATE `tabAOS Video Processing Job` SET status='Cancelled', active_key=NULL, completed_at=%(now)s "
                    "WHERE name IN %(names)s",
                    {"now": now_datetime(), "names": tuple(row.name for row in rows[1:])},
                )
    start = ""
    while True:
        rows = frappe.db.sql(
            """SELECT name, short, generation FROM `tabAOS Video Processing Job`
               WHERE name > %s ORDER BY name LIMIT %s""", (start, _BATCH), as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            generation = max(1, int(row.generation or 0))
            values = {"generation": generation}
            status = frappe.db.get_value("AOS Video Processing Job", row.name, "status")
            values["active_key"] = _digest(row.short) if status in active else None
            frappe.db.set_value("AOS Video Processing Job", row.name, values, update_modified=False)
        start = rows[-1].name


def _backfill_key(doctype: str, condition: str, field: str, source_fields: tuple[str, ...]) -> None:
    start = ""
    fields = ", ".join(("name", *source_fields))
    while True:
        rows = frappe.db.sql(
            f"SELECT {fields} FROM `{_table(doctype)}` WHERE {condition} AND name > %s ORDER BY name LIMIT %s",
            (start, _BATCH), as_dict=True,
        )
        if not rows:
            return
        for row in rows:
            frappe.db.set_value(doctype, row.name, field, _digest(*(row.get(f) for f in source_fields)), update_modified=False)
        start = rows[-1].name


def _backfill_event_keys() -> None:
    if not frappe.db.table_exists("AOS Short Event"):
        return
    start = ""
    while True:
        rows = frappe.db.sql(
            """SELECT name, short, event_type, user, session_id, creation
               FROM `tabAOS Short Event` WHERE name > %s ORDER BY name LIMIT %s""",
            (start, _BATCH), as_dict=True,
        )
        if not rows:
            return
        for row in rows:
            key = _digest(row.short, row.event_type, row.user or row.session_id, row.creation, row.name)
            frappe.db.set_value("AOS Short Event", row.name, "event_key", key, update_modified=False)
        start = rows[-1].name


def _normalize_short_states() -> None:
    if not frappe.db.table_exists("AOS Short"):
        return
    frappe.db.sql("UPDATE `tabAOS Short` SET audience='everyone' WHERE audience IS NULL OR audience NOT IN ('everyone','followers','friends','only_me')")
    cutoff = add_to_date(now_datetime(), hours=-2)
    frappe.db.sql(
        """UPDATE `tabAOS Short` s SET s.status='failed', s.processing_error='PROCESSING_JOB_MISSING'
           WHERE s.status='processing' AND s.modified < %(cutoff)s
             AND NOT EXISTS (SELECT 1 FROM `tabAOS Video Processing Job` j
               WHERE j.short=s.name AND j.status IN ('Queued','Dispatching','Processing'))""",
        {"cutoff": cutoff},
    )


def _reconcile_short_counters() -> None:
    start = ""
    while True:
        rows = frappe.db.sql("SELECT name FROM `tabAOS Short` WHERE name > %s ORDER BY name LIMIT %s", (start, _BATCH), as_dict=True)
        if not rows:
            return
        for row in rows:
            values = {
                "like_count": frappe.db.count("AOS Short Like", {"short": row.name}),
                "save_count": frappe.db.count("AOS Short Save", {"short": row.name}),
                "comment_count": frappe.db.count("AOS Short Comment", {"short": row.name, "status": "active"}),
                "repost_count": frappe.db.count("AOS Short Repost", {"short": row.name, "status": "active"}),
            }
            frappe.db.set_value("AOS Short", row.name, values, update_modified=False)
        start = rows[-1].name
