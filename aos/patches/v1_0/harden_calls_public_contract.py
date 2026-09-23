"""Migrate Calls to opaque public IDs and explicit RTC readiness state.

Post-model-sync, bounded, idempotent, and side-effect free. Existing active
calls from the pre-readiness contract are failed closed because there is no
safe way to prove that their LiveKit room was provisioned and their incoming
signal was durably dispatched under the new contract.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.services.calls.identifiers import generate_public_call_id

BATCH = 200
ACTIVE = ("initiated", "ringing", "ongoing")


def execute() -> None:
    if not frappe.db.table_exists("AOS Call"):
        return
    _require_columns()
    _backfill_public_ids()
    _normalize_state_versions()
    _fail_legacy_active_calls()


def _require_columns() -> None:
    required = (
        "public_id",
        "state_version",
        "rtc_provisioned_at",
        "incoming_dispatched_at",
        "ring_expires_at",
    )
    missing = [column for column in required if not frappe.db.has_column("AOS Call", column)]
    if missing:
        frappe.throw(f"Calls public-contract columns are missing: {', '.join(missing)}")


def _backfill_public_ids() -> None:
    while True:
        names = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Call`
            WHERE public_id IS NULL OR public_id=''
            ORDER BY creation, name
            LIMIT %(batch)s
            """,
            {"batch": BATCH},
            pluck=True,
        )
        if not names:
            return

        for name in names:
            # Collision probability is negligible; retry defensively if an
            # upgraded schema already has the unique constraint installed.
            for _ in range(5):
                public_id = generate_public_call_id()
                if frappe.db.exists("AOS Call", {"public_id": public_id}):
                    continue
                frappe.db.sql(
                    """
                    UPDATE `tabAOS Call`
                    SET public_id=%s
                    WHERE name=%s AND (public_id IS NULL OR public_id='')
                    """,
                    (public_id, name),
                )
                break
            else:
                frappe.throw("Could not allocate an opaque Calls public ID")


def _normalize_state_versions() -> None:
    while True:
        names = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Call`
            WHERE state_version IS NULL OR state_version < 1
            ORDER BY creation, name
            LIMIT %(batch)s
            """,
            {"batch": BATCH},
            pluck=True,
        )
        if not names:
            return
        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET state_version=1
            WHERE name IN %(names)s AND (state_version IS NULL OR state_version < 1)
            """,
            {"names": tuple(names)},
        )


def _fail_legacy_active_calls() -> None:
    now = now_datetime()
    while True:
        names = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Call`
            WHERE status IN %(active)s
              AND COALESCE(is_active,0)=1
              AND (
                    rtc_provisioned_at IS NULL
                    OR incoming_dispatched_at IS NULL
                    OR ring_expires_at IS NULL
                  )
            ORDER BY creation, name
            LIMIT %(batch)s
            """,
            {"active": ACTIVE, "batch": BATCH},
            pluck=True,
        )
        if not names:
            return
        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET status='failed',
                is_active=0,
                ended_at=COALESCE(ended_at,%(now)s),
                duration=CASE
                    WHEN started_at IS NULL THEN GREATEST(COALESCE(duration,0),0)
                    ELSE GREATEST(0,TIMESTAMPDIFF(SECOND,started_at,COALESCE(ended_at,%(now)s)))
                END,
                room_cleanup_pending=1,
                rtc_missing_since=NULL,
                state_version=COALESCE(state_version,1)+1
            WHERE name IN %(names)s
              AND status IN %(active)s
              AND COALESCE(is_active,0)=1
            """,
            {"names": tuple(names), "active": ACTIVE, "now": now},
        )
