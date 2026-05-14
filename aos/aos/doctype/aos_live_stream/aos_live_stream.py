# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


ACTIVE_STATUSES = {"live"}
TERMINAL_STATUSES = {"ended"}

VALID_STATUS_TRANSITIONS = {
    "scheduled": {"live", "ended"},
    "live": {"ended"},
    "ended": set(),
}


class AOSLiveStream(Document):
    def before_insert(self):
        self._set_initial_state()

    def validate(self):
        self._validate_host_user()
        self._validate_status_transition()
        self._validate_single_active_live_per_host()
        self._validate_timestamps()

    def before_save(self):
        self._handle_status_side_effects()
        self._compute_duration()

    def after_insert(self):
        self._set_room_name()

    # VALIDATIONS
    def _validate_host_user(self):
        if not self.host_user:
            frappe.throw("Host user is required.")

        user = frappe.db.get_value(
            "User",
            self.host_user,
            ["name", "enabled"],
            as_dict=True,
        )

        if not user:
            frappe.throw("Invalid host user.")

        if not user.enabled:
            frappe.throw("Host user account is disabled.")

    def _validate_status_transition(self):
        if self.is_new():
            return

        old_status = self.get_db_value("status")

        if old_status == self.status:
            return

        allowed = VALID_STATUS_TRANSITIONS.get(old_status, set())

        if self.status not in allowed:
            frappe.throw(f"Invalid status transition: {old_status} → {self.status}")

    def _validate_single_active_live_per_host(self):
        if self.status not in ACTIVE_STATUSES:
            return

        existing = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Live Stream`
            WHERE host_user = %s
              AND is_active = 1
              AND status IN ('live')
              AND name != %s
            LIMIT 1
            """,
            (self.host_user, self.name or ""),
            as_dict=True,
        )

        if existing:
            frappe.throw("Host already has an active live stream.")

    def _validate_timestamps(self):
        if not self.started_at or not self.ended_at:
            return

        started_at = get_datetime(self.started_at)
        ended_at = get_datetime(self.ended_at)

        if ended_at < started_at:
            frappe.throw("Invalid timestamps: ended_at is before started_at.")

    # SETTERS
    def _set_initial_state(self):
        if not self.status:
            self.status = "scheduled"

        self.is_active = 1 if self.status in ACTIVE_STATUSES else 0

    def _set_room_name(self):
        if not self.room_name:
            self.db_set("room_name", f"live:{self.name}", update_modified=False)

    # STATUS SIDE EFFECTS
    def _handle_status_side_effects(self):
        now = now_datetime()

        if self.status == "live":
            if not self.started_at:
                self.started_at = now

            self.ended_at = None
            self.is_active = 1
            return

        if self.status in TERMINAL_STATUSES:
            if not self.started_at:
                frappe.throw("Cannot end a live stream that never started.")

            if not self.ended_at:
                self.ended_at = now

            self.is_active = 0
            return

        self.is_active = 0

    # COMPUTATIONS
    def _compute_duration(self):
        if not self.started_at or not self.ended_at:
            self.duration_seconds = 0
            return

        started_at = get_datetime(self.started_at)
        ended_at = get_datetime(self.ended_at)

        if ended_at < started_at:
            frappe.throw("Invalid timestamps: ended_at is before started_at.")

        delta = ended_at - started_at
        self.duration_seconds = int(delta.total_seconds())
