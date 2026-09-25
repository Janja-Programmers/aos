"""Reconcile persisted Activity rows with the current read-model invariants.

This is a one-time current-schema reconciliation for sites that already contain
Activity rows when the hardened schema lands.  It is not a compatibility
adapter: unsupported event types are removed and only the canonical taxonomy is
retained before uniqueness indexes are installed.
"""

from __future__ import annotations

import json

import frappe

from aos.services.activity.constants import (
    ACTIVE_STATUS,
    ACTIVITY_COUNT_MAX,
    EVENT_MODE_COALESCE,
    EVENT_MODE_ONCE,
    EVENT_SPECS,
    VALID_ACTIVITY_STATUSES,
)
from aos.services.activity.identity import new_activity_id, normalize_activity_id
from aos.services.activity_service import ActivityService

_BATCH_SIZE = 500
_DOCTYPE = "AOS User Activity"


def execute() -> None:
    if not frappe.db.table_exists(_DOCTYPE):
        return

    _remove_noncanonical_rows()
    _normalize_current_rows()
    _backfill_public_ids()
    _canonicalize_resource_references()
    _bound_metadata_to_taxonomy()
    _hash_hidden_unique_keys()
    _reconcile_coalesced_active_duplicates()
    _reconcile_one_off_duplicates()
    _backfill_integrity_keys()


def _remove_noncanonical_rows() -> None:
    current_types = tuple(sorted(EVENT_SPECS))
    frappe.db.sql(
        f"DELETE FROM `tab{_DOCTYPE}` WHERE activity_type NOT IN %(types)s OR COALESCE(unique_key, '') = ''",
        {"types": current_types},
    )


def _normalize_current_rows() -> None:
    statuses = tuple(sorted(VALID_ACTIVITY_STATUSES))
    frappe.db.sql(
        f"""
        UPDATE `tab{_DOCTYPE}`
        SET status = CASE WHEN status IN %(statuses)s THEN status ELSE %(active)s END,
            `count` = LEAST(GREATEST(COALESCE(`count`, 0), 1), %(count_max)s),
            occurred_at = COALESCE(occurred_at, creation),
            last_occurrence_at = COALESCE(last_occurrence_at, occurred_at, creation),
            active_key = NULL,
            event_key = NULL
        """,
        {"statuses": statuses, "active": ACTIVE_STATUS, "count_max": ACTIVITY_COUNT_MAX},
    )
    for event_type, spec in EVENT_SPECS.items():
        frappe.db.sql(
            f"""
            UPDATE `tab{_DOCTYPE}`
            SET activity_group=%s, route_type=%s, target_doctype=%s
            WHERE activity_type=%s
            """,
            (str(spec["group"]), str(spec["route_type"]), str(spec["target_doctype"]), event_type),
        )


def _backfill_public_ids() -> None:
    start_after = ""
    while True:
        rows = frappe.db.sql(
            f"""
            SELECT name, public_id
            FROM `tab{_DOCTYPE}`
            WHERE name > %s
            ORDER BY name ASC
            LIMIT %s
            """,
            (start_after, _BATCH_SIZE),
            as_dict=True,
        )
        if not rows:
            return
        for row in rows:
            if normalize_activity_id(row.public_id):
                continue
            frappe.db.set_value(
                _DOCTYPE,
                row.name,
                "public_id",
                new_activity_id(),
                update_modified=False,
            )
        start_after = str(rows[-1].name)


def _canonicalize_resource_references() -> None:
    # Ads expose AOS Ad.public_id; the internal target_name remains the Ad row.
    if frappe.db.table_exists("AOS Ad") and frappe.db.has_column("AOS Ad", "public_id"):
        frappe.db.sql(
            f"""
            UPDATE `tab{_DOCTYPE}` ua
            INNER JOIN `tabAOS Ad` a ON a.name = ua.target_name
            SET ua.route_id = a.public_id
            WHERE ua.route_type = 'ad' AND COALESCE(a.public_id, '') != ''
            """
        )

    # Profile routes expose ACC-* while target_name keeps the internal User link
    # identity for lifecycle cleanup.  The profile mapping is authoritative.
    if frappe.db.table_exists("AOS Profile"):
        frappe.db.sql(
            f"""
            UPDATE `tab{_DOCTYPE}` ua
            INNER JOIN `tabAOS Profile` p ON p.name = ua.route_id
            SET ua.target_doctype = 'User', ua.target_name = p.user
            WHERE ua.route_type = 'profile' AND COALESCE(p.user, '') != ''
            """
        )


def _metadata_object(raw) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _bound_metadata_to_taxonomy() -> None:
    start_after = ""
    while True:
        rows = frappe.db.sql(
            f"""
            SELECT name, activity_type, metadata_json
            FROM `tab{_DOCTYPE}`
            WHERE name > %s
            ORDER BY name ASC
            LIMIT %s
            """,
            (start_after, _BATCH_SIZE),
            as_dict=True,
        )
        if not rows:
            return
        delete_names: list[str] = []
        for row in rows:
            metadata = _metadata_object(row.metadata_json)
            try:
                bounded = ActivityService._bounded_metadata(
                    activity_type=str(row.activity_type),
                    metadata=metadata,
                )
            except (TypeError, ValueError):
                # Rows that cannot satisfy the current canonical event schema
                # are not historical contracts; drop them instead of carrying
                # malformed/identity-leaking metadata forward.
                delete_names.append(str(row.name))
                continue
            if bounded != metadata:
                frappe.db.set_value(
                    _DOCTYPE,
                    row.name,
                    "metadata_json",
                    frappe.as_json(bounded),
                    update_modified=False,
                )
        if delete_names:
            frappe.db.sql(
                f"DELETE FROM `tab{_DOCTYPE}` WHERE name IN %(names)s",
                {"names": tuple(delete_names)},
            )
        start_after = str(rows[-1].name)


def _hash_hidden_unique_keys() -> None:
    # Earlier rows may contain plaintext internal resource/User/report values in
    # unique_key. Current producers always persist SHA-256. Preserve already
    # hashed 64-hex keys so rows whose original material exceeded the old field
    # limit do not get double-hashed.
    frappe.db.sql(
        f"""
        UPDATE `tab{_DOCTYPE}`
        SET unique_key = CASE
            WHEN unique_key REGEXP '^[0-9a-f]{{64}}$' THEN unique_key
            ELSE SHA2(unique_key, 256)
        END
        WHERE COALESCE(unique_key, '') != ''
        """
    )


def _reconcile_coalesced_active_duplicates() -> None:
    coalesced = tuple(sorted(event for event, spec in EVENT_SPECS.items() if spec["mode"] == EVENT_MODE_COALESCE))
    while True:
        groups = frappe.db.sql(
            f"""
            SELECT user, unique_key
            FROM `tab{_DOCTYPE}`
            WHERE status=%(active)s AND activity_type IN %(types)s
            GROUP BY user, unique_key
            HAVING COUNT(*) > 1
            ORDER BY user, unique_key
            LIMIT %(limit)s
            """,
            {"active": ACTIVE_STATUS, "types": coalesced, "limit": _BATCH_SIZE},
            as_dict=True,
        )
        if not groups:
            return
        for group in groups:
            rows = frappe.db.sql(
                f"""
                SELECT name, `count`, occurred_at, last_occurrence_at
                FROM `tab{_DOCTYPE}`
                WHERE user=%s AND unique_key=%s AND status=%s
                ORDER BY last_occurrence_at DESC, creation DESC, name DESC
                """,
                (group.user, group.unique_key, ACTIVE_STATUS),
                as_dict=True,
            )
            keeper = rows[0]
            duplicates = tuple(str(row.name) for row in rows[1:])
            if not duplicates:
                continue
            occurred = [row.occurred_at for row in rows if row.occurred_at]
            last = [row.last_occurrence_at or row.occurred_at for row in rows if row.last_occurrence_at or row.occurred_at]
            frappe.db.set_value(
                _DOCTYPE,
                keeper.name,
                {
                    "count": min(sum(max(int(row.count or 0), 1) for row in rows), ACTIVITY_COUNT_MAX),
                    "occurred_at": min(occurred) if occurred else keeper.occurred_at,
                    "last_occurrence_at": max(last) if last else keeper.last_occurrence_at,
                },
                update_modified=False,
            )
            frappe.db.sql(
                f"UPDATE `tab{_DOCTYPE}` SET status='Hidden', active_key=NULL WHERE name IN %(names)s",
                {"names": duplicates},
            )


def _reconcile_one_off_duplicates() -> None:
    one_off = tuple(sorted(event for event, spec in EVENT_SPECS.items() if spec["mode"] == EVENT_MODE_ONCE))
    while True:
        groups = frappe.db.sql(
            f"""
            SELECT user, unique_key
            FROM `tab{_DOCTYPE}`
            WHERE activity_type IN %(types)s
            GROUP BY user, unique_key
            HAVING COUNT(*) > 1
            ORDER BY user, unique_key
            LIMIT %(limit)s
            """,
            {"types": one_off, "limit": _BATCH_SIZE},
            as_dict=True,
        )
        if not groups:
            return
        for group in groups:
            names = frappe.db.sql(
                f"""
                SELECT name
                FROM `tab{_DOCTYPE}`
                WHERE user=%(user)s AND unique_key=%(unique_key)s AND activity_type IN %(types)s
                ORDER BY modified DESC, creation DESC, name DESC
                """,
                {"types": one_off, "user": group.user, "unique_key": group.unique_key},
                pluck=True,
            )
            duplicates = tuple(str(name) for name in names[1:] if name)
            if duplicates:
                frappe.db.sql(
                    f"DELETE FROM `tab{_DOCTYPE}` WHERE name IN %(names)s",
                    {"names": duplicates},
                )


def _backfill_integrity_keys() -> None:
    coalesced = tuple(sorted(event for event, spec in EVENT_SPECS.items() if spec["mode"] == EVENT_MODE_COALESCE))
    one_off = tuple(sorted(event for event, spec in EVENT_SPECS.items() if spec["mode"] == EVENT_MODE_ONCE))
    # DB-generated SHA-256 keys avoid process-local coordination and keep this
    # reconciliation bounded to two set-based writes after duplicates are gone.
    frappe.db.sql(
        f"""
        UPDATE `tab{_DOCTYPE}`
        SET active_key = CASE
                WHEN status=%(active)s AND activity_type IN %(coalesced)s
                THEN SHA2(CONCAT(user, CHAR(31), unique_key), 256)
                ELSE NULL
            END,
            event_key = CASE
                WHEN activity_type IN %(one_off)s
                THEN SHA2(CONCAT('once', CHAR(31), user, CHAR(31), unique_key), 256)
                ELSE NULL
            END
        """,
        {"active": ACTIVE_STATUS, "coalesced": coalesced, "one_off": one_off},
    )
