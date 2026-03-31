# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, get_datetime, time_diff_in_seconds


class AOSLiveStreamView(Document):
    def validate(self):
        self._validate_identity()
        self._validate_single_active_session()

    def before_insert(self):
        self._set_initial_state()

    def before_save(self):
        self._handle_leave()
        self._compute_duration()
        self._update_last_seen()

    # VALIDATIONS
    def _validate_identity(self):
        """Ensure either user or session_id exists."""
        if not self.user and not self.session_id:
            frappe.throw("Either user or session_id is required")

    def _validate_single_active_session(self):
        """Prevent duplicate active sessions for same user/session in a live."""
        if self.is_new():
            filters = {
                "live_stream": self.live_stream,
                "is_active": 1,
            }

            if self.user:
                filters["user"] = self.user
            else:
                filters["session_id"] = self.session_id

            existing = frappe.db.exists(
                "AOS Live Stream View",
                {
                    **filters,
                    "name": ["!=", self.name or ""],
                },
            )

            if existing:
                frappe.throw("Active session already exists for this user/session")

    # SETTERS
    def _set_initial_state(self):
        if not self.joined_at:
            self.joined_at = now_datetime()

        self.is_active = 1

    # STATUS HANDLING
    def _handle_leave(self):
        """Handle when user leaves the stream."""
        if self.left_at and self.is_active:
            self.is_active = 0

    def _update_last_seen(self):
        """Update last seen timestamp for heartbeat tracking."""
        if not self.last_seen_at:
            self.last_seen_at = now_datetime()

    # COMPUTATIONS
    def _compute_duration(self):
        """Compute watch duration when session ends."""
        if not self.joined_at:
            return

        end_time = self.left_at or now_datetime()

        joined_at = get_datetime(self.joined_at)
        end_time = get_datetime(end_time)

        duration = time_diff_in_seconds(end_time, joined_at)
        self.watch_duration_seconds = int(duration or 0)

        # Mark qualified
        if self.watch_duration_seconds >= 5:
            self.qualified = 1
