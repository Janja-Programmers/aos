# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime, time_diff_in_seconds


LIVE_STATUS = "live"
QUALIFIED_VIEW_SECONDS = 5


class AOSLiveStreamView(Document):
    def before_insert(self):
        self._set_initial_state()

    def validate(self):
        self._validate_required_fields()
        self._validate_optional_user()
        self._validate_live_is_active()
        self._validate_timestamps()
        self._validate_single_active_session()

    def before_save(self):
        self._handle_leave()
        self._compute_duration()
        self._update_last_seen()

    # VALIDATIONS
    def _validate_required_fields(self):
        if not self.live_stream:
            frappe.throw("Live stream is required.")

        if not self.session_id:
            frappe.throw("Session id is required.")

        if not self.joined_at:
            frappe.throw("Joined at is required.")

    def _validate_optional_user(self):
        if not self.user:
            return

        user = frappe.db.get_value(
            "User",
            self.user,
            ["name", "enabled"],
            as_dict=True,
        )

        if not user:
            frappe.throw("Invalid user.")

        if not user.enabled:
            frappe.throw("User account is disabled.")

    def _validate_live_is_active(self):
        live = frappe.db.get_value(
            "AOS Live Stream",
            self.live_stream,
            ["name", "status", "is_active"],
            as_dict=True,
        )

        if not live:
            frappe.throw("Invalid live stream.")

        # Existing view rows may be saved during leave/end cleanup.
        # Only block new active view sessions from joining inactive lives.
        if self.is_new() and (live.status != LIVE_STATUS or not live.is_active):
            frappe.throw("Cannot join inactive live stream.")

    def _validate_timestamps(self):
        if not self.joined_at:
            return

        joined_at = get_datetime(self.joined_at)

        if self.left_at:
            left_at = get_datetime(self.left_at)

            if left_at < joined_at:
                frappe.throw("Invalid timestamps: left_at is before joined_at.")

        if self.last_seen_at:
            last_seen_at = get_datetime(self.last_seen_at)

            if last_seen_at < joined_at:
                frappe.throw("Invalid timestamps: last_seen_at is before joined_at.")

    def _validate_single_active_session(self):
        """
        Prevent duplicate active sessions for the same live/session_id.

        session_id is the canonical viewer session identity because guests have
        no user. Logged-in users also still carry a session_id, so this remains
        consistent for both guest and authenticated viewers.
        """
        if not self.is_new():
            return

        existing = frappe.db.exists(
            "AOS Live Stream View",
            {
                "live_stream": self.live_stream,
                "session_id": self.session_id,
                "is_active": 1,
                "name": ["!=", self.name or ""],
            },
        )

        if existing:
            frappe.throw("Active session already exists for this live stream.")

    # SETTERS
    def _set_initial_state(self):
        now = now_datetime()

        if not self.joined_at:
            self.joined_at = now

        if not self.last_seen_at:
            self.last_seen_at = now

        if not self.left_at:
            self.is_active = 1

    # STATUS HANDLING
    def _handle_leave(self):
        if self.left_at:
            self.is_active = 0

    def _update_last_seen(self):
        """
        Keep heartbeat tracking fresh while the session is active.

        For ended sessions, last_seen_at should not move past left_at.
        """
        if self.left_at:
            if not self.last_seen_at or get_datetime(self.last_seen_at) > get_datetime(self.left_at):
                self.last_seen_at = self.left_at
            return

        if self.is_active:
            self.last_seen_at = now_datetime()

    # COMPUTATIONS
    def _compute_duration(self):
        if not self.joined_at:
            self.watch_duration_seconds = 0
            self.qualified = 0
            return

        joined_at = get_datetime(self.joined_at)
        end_time = get_datetime(self.left_at or now_datetime())

        if end_time < joined_at:
            frappe.throw("Invalid timestamps: watch end time is before joined_at.")

        duration = time_diff_in_seconds(end_time, joined_at)
        self.watch_duration_seconds = max(int(duration or 0), 0)

        self.qualified = 1 if self.watch_duration_seconds >= QUALIFIED_VIEW_SECONDS else 0
