"""Reconcile AOS Short Event keys before restoring their unique index.

The unique event-key index was added to an older patch after some sites had
already recorded that patch as executed.  Those sites could therefore accept
duplicate idempotency keys.  This data-only patch makes the table safe for the
schema-only restoration patch that follows it.
"""
from __future__ import annotations

import hashlib

import frappe

_BATCH = 500
_DUPLICATE_GROUP_BATCH = 200


def _legacy_key(*parts: object) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _backfill_missing_event_keys() -> None:
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, short, event_type, user, session_id, creation
            FROM `tabAOS Short Event`
            WHERE event_key IS NULL OR event_key = ''
            ORDER BY name
            LIMIT %s
            """,
            (_BATCH,),
            as_dict=True,
        )
        if not rows:
            return

        for row in rows:
            # Include the immutable row name so legacy/manual events cannot
            # collide with canonical client-event keys or with one another.
            key = _legacy_key(
                "legacy-short-event",
                row.name,
                row.short,
                row.event_type,
                row.user or row.session_id,
                row.creation,
            )
            frappe.db.set_value(
                "AOS Short Event",
                row.name,
                "event_key",
                key,
                update_modified=False,
            )


def _deduplicate_event_keys() -> None:
    while True:
        duplicate_keys = frappe.db.sql(
            """
            SELECT event_key
            FROM `tabAOS Short Event`
            WHERE event_key IS NOT NULL AND event_key != ''
            GROUP BY event_key
            HAVING COUNT(*) > 1
            ORDER BY event_key
            LIMIT %s
            """,
            (_DUPLICATE_GROUP_BATCH,),
            pluck=True,
        )
        if not duplicate_keys:
            return

        for key in duplicate_keys:
            keep = frappe.db.sql(
                """
                SELECT name
                FROM `tabAOS Short Event`
                WHERE event_key = %s
                ORDER BY creation ASC, name ASC
                LIMIT 1
                """,
                (key,),
                pluck=True,
            )
            if not keep:
                continue
            keep_name = keep[0]

            while True:
                duplicate_names = frappe.db.sql(
                    """
                    SELECT name
                    FROM `tabAOS Short Event`
                    WHERE event_key = %s AND name != %s
                    ORDER BY creation ASC, name ASC
                    LIMIT %s
                    """,
                    (key, keep_name, _BATCH),
                    pluck=True,
                )
                if not duplicate_names:
                    break
                # Duplicate event_key rows represent the same idempotent event.
                # Preserve the earliest row and remove only duplicate copies.
                frappe.db.delete(
                    "AOS Short Event",
                    {"name": ["in", duplicate_names]},
                )


def execute() -> None:
    if not frappe.db.table_exists("AOS Short Event"):
        return
    if not frappe.db.has_column("AOS Short Event", "event_key"):
        return

    _backfill_missing_event_keys()
    _deduplicate_event_keys()
