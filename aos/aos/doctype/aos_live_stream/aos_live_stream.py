# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


LIVE_STREAM_DOCTYPE = "AOS Live Stream"
LIVE_VIEW_DOCTYPE = "AOS Live Stream View"

LIVE_STATUS = "live"
ENDED_STATUS = "ended"

ACTIVE_STATUSES = {
    LIVE_STATUS,
}

TERMINAL_STATUSES = {
    ENDED_STATUS,
}

VALID_STATUS_TRANSITIONS = {
    "scheduled": {
        LIVE_STATUS,
        ENDED_STATUS,
    },
    LIVE_STATUS: {
        ENDED_STATUS,
    },
    ENDED_STATUS: set(),
}


class AOSLiveStream(Document):
    def before_insert(self):
        self._set_initial_state()

    def validate(self):
        self._validate_host_user()
        self._validate_immutable_ownership()
        self._validate_status_transition()
        self._validate_single_active_live_per_host()
        self._validate_timestamps()

    def before_save(self):
        self._handle_status_side_effects()
        self._sync_active_host_key()
        self._compute_duration()

    def after_insert(self):
        self._set_room_name()

    def on_update(self):
        """Make termination O(1); finalize large viewer sets asynchronously.

        Once ``status=ended`` is durable, participant APIs reject further
        interaction. Closing thousands of historical view rows inside this
        request only increases lock time, so the bounded finalizer owns that
        work after commit.
        """
        if not self._has_transitioned_to_ended():
            return

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
            # The recurring Live reconciler discovers terminal streams that
            # still have active view rows, so queue degradation is recoverable.
            frappe.log_error(
                frappe.get_traceback(),
                "Live view finalization enqueue failed",
            )

    # VALIDATIONS
    def _validate_host_user(self):
        if not self.host_user:
            frappe.throw(
                "Host user is required."
            )

        user = frappe.db.get_value(
            "User",
            self.host_user,
            [
                "name",
                "enabled",
            ],
            as_dict=True,
        )

        if not user:
            frappe.throw(
                "Invalid host user."
            )

        if not bool(user.enabled):
            frappe.throw(
                "Host user account is disabled."
            )

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
            return

        old_status = self.get_db_value(
            "status"
        )

        if old_status == self.status:
            return

        allowed = VALID_STATUS_TRANSITIONS.get(
            old_status,
            set(),
        )

        if self.status not in allowed:
            frappe.throw(
                f"Invalid status transition: "
                f"{old_status} → {self.status}"
            )

    def _validate_single_active_live_per_host(self):
        if self.status not in ACTIVE_STATUSES:
            return

        existing = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Live Stream`
            WHERE host_user = %s
              AND is_active = 1
              AND status = %s
              AND name != %s
            LIMIT 1
            """,
            (
                self.host_user,
                LIVE_STATUS,
                self.name or "",
            ),
            as_dict=True,
        )

        if existing:
            frappe.throw(
                "Host already has an active live stream."
            )

    def _validate_timestamps(self):
        if not self.started_at or not self.ended_at:
            return

        started_at = get_datetime(
            self.started_at
        )

        ended_at = get_datetime(
            self.ended_at
        )

        if ended_at < started_at:
            frappe.throw(
                "Invalid timestamps: ended_at is before started_at."
            )

    # SETTERS
    def _set_initial_state(self):
        if not self.status:
            self.status = "scheduled"

        self.is_active = int(
            self.status in ACTIVE_STATUSES
        )
        self.active_host_key = self.host_user if self.is_active else None

    def _sync_active_host_key(self):
        self.active_host_key = self.host_user if self.status in ACTIVE_STATUSES and bool(self.is_active) else None

    def _set_room_name(self):
        if self.room_name:
            return

        self.db_set(
            "room_name",
            f"live:{self.name}",
            update_modified=False,
        )

    # STATUS SIDE EFFECTS
    def _handle_status_side_effects(self):
        now = now_datetime()

        if self.status == LIVE_STATUS:
            if not self.started_at:
                self.started_at = now

            self.ended_at = None
            self.duration_seconds = 0
            self.is_active = 1
            return

        if self.status in TERMINAL_STATUSES:
            if not self.started_at:
                frappe.throw(
                    "Cannot end a live stream that never started."
                )

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

        started_at = get_datetime(
            self.started_at
        )

        ended_at = get_datetime(
            self.ended_at
        )

        if ended_at < started_at:
            frappe.throw(
                "Invalid timestamps: ended_at is before started_at."
            )

        delta = ended_at - started_at

        self.duration_seconds = max(
            int(delta.total_seconds()),
            0,
        )

    # HELPERS
    def _has_transitioned_to_ended(self) -> bool:
        """
        Return True only when an existing stream has just transitioned
        from a non-ended state to ended.
        """

        if self.status != ENDED_STATUS:
            return False

        previous = self.get_doc_before_save()

        if not previous:
            return False

        return previous.status != ENDED_STATUS
