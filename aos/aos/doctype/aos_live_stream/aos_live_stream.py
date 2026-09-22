# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from aos.utils.identifiers import new_prefixed_name

LIVE_STREAM_DOCTYPE = "AOS Live Stream"

STARTING_STATUS = "starting"
LIVE_STATUS = "live"
ENDED_STATUS = "ended"
FAILED_STATUS = "failed"

ACTIVE_STATUSES = {LIVE_STATUS}
HOST_RESERVATION_STATUSES = {STARTING_STATUS, LIVE_STATUS}
TERMINAL_STATUSES = {ENDED_STATUS, FAILED_STATUS}

VALID_STATUS_TRANSITIONS = {
    STARTING_STATUS: {LIVE_STATUS, ENDED_STATUS, FAILED_STATUS},
    LIVE_STATUS: {ENDED_STATUS},
    ENDED_STATUS: set(),
    FAILED_STATUS: set(),
}


class AOSLiveStream(Document):
    """Canonical application lifecycle for one AOS Live session.

    ``starting`` reserves the host while LiveKit room provisioning is in flight.
    Only ``live`` is joinable/publishable. ``ended`` and ``failed`` are terminal.
    External room cleanup is tracked separately by ``room_cleanup_pending`` so a
    dependency outage can never reopen or resurrect application state.
    """

    def autoname(self):
        self.name = new_prefixed_name("LIVE")

    def before_insert(self):
        self._set_initial_state()

    def validate(self):
        self._validate_host_user()
        self._validate_immutable_ownership()
        self._validate_status_transition()
        self._validate_single_host_reservation()
        self._validate_timestamps()

    def before_save(self):
        self._handle_status_side_effects()
        self._sync_active_host_key()
        self._compute_duration()

    def after_insert(self):
        self._set_room_name()

    def on_update(self):
        if not self._has_transitioned_to_ended():
            return

        # Once application state is terminal no participant API accepts new
        # activity. Large historical view sets are finalized in bounded jobs.
        frappe.db.set_value(
            LIVE_STREAM_DOCTYPE,
            self.name,
            "viewer_count",
            0,
            update_modified=False,
        )
        try:
            frappe.enqueue(
                "aos.tasks.live.finalize_ended_live_views",
                queue="short",
                enqueue_after_commit=True,
                live_id=self.name,
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Live view finalization enqueue failed",
            )

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
        if not bool(user.enabled):
            frappe.throw("Host user account is disabled.")

    def _validate_immutable_ownership(self):
        if self.is_new():
            return
        previous = self.get_doc_before_save()
        if not previous:
            return
        if previous.host_user != self.host_user:
            frappe.throw("Live host cannot be changed.")
        if previous.room_name and previous.room_name != self.room_name:
            frappe.throw("LiveKit room name cannot be changed.")

    def _validate_status_transition(self):
        if self.is_new():
            if self.status != STARTING_STATUS:
                frappe.throw("A new Live session must start in starting state.")
            return

        old_status = self.get_db_value("status")
        if old_status == self.status:
            return
        if self.status not in VALID_STATUS_TRANSITIONS.get(old_status, set()):
            frappe.throw(f"Invalid status transition: {old_status} → {self.status}")

    def _validate_single_host_reservation(self):
        if self.status not in HOST_RESERVATION_STATUSES:
            return
        existing = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Live Stream`
            WHERE host_user = %s
              AND active_host_key IS NOT NULL
              AND name != %s
            LIMIT 1
            """,
            (self.host_user, self.name or ""),
            as_dict=True,
        )
        if existing:
            frappe.throw("Host already has a Live session starting or active.")

    def _validate_timestamps(self):
        if not self.started_at or not self.ended_at:
            return
        if get_datetime(self.ended_at) < get_datetime(self.started_at):
            frappe.throw("Invalid timestamps: ended_at is before started_at.")

    def _set_initial_state(self):
        if not self.status:
            self.status = STARTING_STATUS
        self.is_active = int(self.status == LIVE_STATUS)
        self.active_host_key = (
            self.host_user if self.status in HOST_RESERVATION_STATUSES else None
        )

    def _sync_active_host_key(self):
        self.active_host_key = (
            self.host_user if self.status in HOST_RESERVATION_STATUSES else None
        )

    def _set_room_name(self):
        if self.room_name:
            return
        self.db_set("room_name", f"live:{self.name}", update_modified=False)

    def _handle_status_side_effects(self):
        now = now_datetime()

        if self.status == STARTING_STATUS:
            self.started_at = None
            self.ended_at = None
            self.duration_seconds = 0
            self.is_active = 0
            return

        if self.status == LIVE_STATUS:
            if not self.started_at:
                self.started_at = now
            self.ended_at = None
            self.duration_seconds = 0
            self.is_active = 1
            return

        if self.status in TERMINAL_STATUSES:
            if not self.ended_at:
                self.ended_at = now
            self.is_active = 0
            return

        frappe.throw("Invalid Live status.")

    def _compute_duration(self):
        if not self.started_at or not self.ended_at:
            self.duration_seconds = 0
            return
        started_at = get_datetime(self.started_at)
        ended_at = get_datetime(self.ended_at)
        if ended_at < started_at:
            frappe.throw("Invalid timestamps: ended_at is before started_at.")
        self.duration_seconds = max(int((ended_at - started_at).total_seconds()), 0)

    def _has_transitioned_to_ended(self) -> bool:
        if self.status != ENDED_STATUS:
            return False
        previous = self.get_doc_before_save()
        return bool(previous and previous.status != ENDED_STATUS)
