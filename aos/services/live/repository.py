"""Bounded database primitives for Live lifecycle and discovery."""

from __future__ import annotations

from typing import Any

import frappe

from .constants import LIVE_DOCTYPE


class LiveRepository:
    def lock_live(self, live_id: str) -> dict[str, Any] | None:
        rows = frappe.db.sql(
            """
            SELECT name, host_user, status, is_active, room_name, started_at, ended_at
            FROM `tabAOS Live Stream`
            WHERE name = %s
            LIMIT 1 FOR UPDATE
            """,
            (live_id,),
            as_dict=True,
        )
        return dict(rows[0]) if rows else None

    def lock_live_shared(self, live_id: str) -> dict[str, Any] | None:
        """Hold a shared lifecycle lock for high-frequency read-side events.

        Concurrent reactions may proceed together, while an end/demotion path
        requiring an exclusive row lock waits until accepted reactions finish.
        """
        rows = frappe.db.sql(
            """
            SELECT name, host_user, status, is_active, room_name, started_at, ended_at
            FROM `tabAOS Live Stream`
            WHERE name = %s
            LIMIT 1 LOCK IN SHARE MODE
            """,
            (live_id,),
            as_dict=True,
        )
        return dict(rows[0]) if rows else None

    def lock_active_host_live(self, host_user: str) -> list[dict[str, Any]]:
        rows = frappe.db.sql(
            """
            SELECT name, status, is_active, started_at
            FROM `tabAOS Live Stream`
            WHERE host_user = %s AND active_host_key IS NOT NULL
            ORDER BY started_at ASC, creation ASC, name ASC
            LIMIT 2 FOR UPDATE
            """,
            (host_user,),
            as_dict=True,
        )
        return [dict(row) for row in rows]

    def active_room_names(self, *, limit: int = 100) -> list[str]:
        return frappe.get_all(
            LIVE_DOCTYPE,
            filters={"status": "live", "is_active": 1},
            pluck="room_name",
            order_by="modified asc, name asc",
            limit_page_length=max(1, min(int(limit), 500)),
        )
