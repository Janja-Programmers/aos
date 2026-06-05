# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime, time_diff_in_seconds

LIVE_STREAM_DOCTYPE = "AOS Live Stream"
LIVE_VIEW_DOCTYPE = "AOS Live Stream View"
USER_DOCTYPE = "User"

LIVE_STATUS = "live"
QUALIFIED_VIEW_SECONDS = 5


class AOSLiveStreamView(Document):
    def before_insert(self):
        self._set_initial_state()

    def validate(self):
        self._normalize_values()
        self._validate_required_fields()
        self._validate_optional_user()
        self._validate_immutable_identity()
        self._validate_live_state()
        self._validate_session_state()
        self._validate_timestamps()
        self._validate_single_active_session()

    def before_save(self):
        self._handle_leave()
        self._update_last_seen()
        self._compute_duration()

    # NORMALIZATION
    def _normalize_values(self):
        if self.session_id is not None:
            self.session_id = str(
                self.session_id
            ).strip()

        self.is_active = int(
            bool(self.is_active)
        )

    # VALIDATIONS
    def _validate_required_fields(self):
        if not self.live_stream:
            frappe.throw(
                "Live stream is required."
            )

        if not self.session_id:
            frappe.throw(
                "Session id is required."
            )

        if not self.joined_at:
            frappe.throw(
                "Joined at is required."
            )

    def _validate_optional_user(self):
        if not self.user:
            return

        user = frappe.db.get_value(
            USER_DOCTYPE,
            self.user,
            [
                "name",
                "enabled",
            ],
            as_dict=True,
        )

        if not user:
            frappe.throw(
                "Invalid user."
            )

        if not bool(user.enabled):
            frappe.throw(
                "User account is disabled."
            )

    def _validate_immutable_identity(self):
        """
        A view row represents one immutable watch session.

        Once inserted, its live stream, session ID, and authenticated user
        must not be reassigned.
        """
        if self.is_new():
            return

        previous = self.get_doc_before_save()

        if not previous:
            return

        if previous.live_stream != self.live_stream:
            frappe.throw(
                "Live stream cannot be changed for an existing view session."
            )

        if previous.session_id != self.session_id:
            frappe.throw(
                "Session id cannot be changed for an existing view session."
            )

        if previous.user != self.user:
            frappe.throw(
                "Viewer user cannot be changed for an existing view session."
            )

        if previous.joined_at != self.joined_at:
            frappe.throw(
                "Joined at cannot be changed for an existing view session."
            )

    def _validate_live_state(self):
        live = frappe.db.get_value(
            LIVE_STREAM_DOCTYPE,
            self.live_stream,
            [
                "name",
                "status",
                "is_active",
                "ended_at",
            ],
            as_dict=True,
        )

        if not live:
            frappe.throw(
                "Invalid live stream."
            )

        # Only a new active view session requires an active live stream.
        if (
            self.is_new()
            and (
                live.status != LIVE_STATUS
                or not bool(live.is_active)
            )
        ):
            frappe.throw(
                "Cannot join an inactive live stream."
            )

        # Existing rows may be saved while being closed after the live ends.
        if not self.is_new():
            return

        if self.left_at:
            frappe.throw(
                "A new view session cannot already be closed."
            )

    def _validate_session_state(self):
        """
        Enforce a one-way session lifecycle:

        active:
            is_active = 1
            left_at = empty

        closed:
            is_active = 0
            left_at = set
        """
        previous = (
            self.get_doc_before_save()
            if not self.is_new()
            else None
        )

        # A closed session cannot be reopened.
        if (
            previous
            and (
                not bool(previous.is_active)
                or previous.left_at
            )
            and (
                bool(self.is_active)
                or not self.left_at
            )
        ):
            frappe.throw(
                "A closed live view session cannot be reopened."
            )

        if bool(self.is_active) and self.left_at:
            frappe.throw(
                "An active view session cannot have left_at."
            )

        if not bool(self.is_active) and not self.left_at:
            frappe.throw(
                "A closed view session must have left_at."
            )

    def _validate_timestamps(self):
        if not self.joined_at:
            return

        joined_at = get_datetime(
            self.joined_at
        )

        if self.left_at:
            left_at = get_datetime(
                self.left_at
            )

            if left_at < joined_at:
                frappe.throw(
                    "Invalid timestamps: left_at is before joined_at."
                )

        if self.last_seen_at:
            last_seen_at = get_datetime(
                self.last_seen_at
            )

            if last_seen_at < joined_at:
                frappe.throw(
                    "Invalid timestamps: last_seen_at is before joined_at."
                )

            if (
                self.left_at
                and last_seen_at
                > get_datetime(self.left_at)
            ):
                frappe.throw(
                    "Invalid timestamps: last_seen_at is after left_at."
                )

    def _validate_single_active_session(self):
        """
        Prevent duplicate active rows for the same live/session pair.

        session_id is the canonical session identity for both guests and
        authenticated viewers.
        """
        if not bool(self.is_active):
            return

        filters = {
            "live_stream": self.live_stream,
            "session_id": self.session_id,
            "is_active": 1,
            "name": [
                "!=",
                self.name or "",
            ],
        }

        # For authenticated sessions, retain viewer ownership as an
        # additional consistency constraint.
        if self.user:
            filters["user"] = self.user

        existing = frappe.db.exists(
            LIVE_VIEW_DOCTYPE,
            filters,
        )

        if existing:
            frappe.throw(
                "Active session already exists for this live stream."
            )

    # INITIAL STATE
    def _set_initial_state(self):
        now = now_datetime()

        if not self.joined_at:
            self.joined_at = now

        if not self.last_seen_at:
            self.last_seen_at = self.joined_at

        if not self.left_at:
            self.is_active = 1
        else:
            self.is_active = 0

        self.watch_duration_seconds = 0
        self.qualified = 0

    # STATUS HANDLING
    def _handle_leave(self):
        """
        Normalize session state before saving.

        Setting left_at closes the session. A session explicitly marked
        inactive without left_at is closed at the current time.
        """
        if self.left_at:
            self.is_active = 0
            return

        if not bool(self.is_active):
            self.left_at = now_datetime()
            self.is_active = 0
            return

        self.is_active = 1

    def _update_last_seen(self):
        """
        Keep heartbeat time current while active.

        Closed sessions use left_at as their final last-seen timestamp.
        """
        if self.left_at:
            self.last_seen_at = self.left_at
            return

        if bool(self.is_active):
            self.last_seen_at = now_datetime()

    # COMPUTATIONS
    def _compute_duration(self):
        if not self.joined_at:
            self.watch_duration_seconds = 0
            self.qualified = 0
            return

        joined_at = get_datetime(
            self.joined_at
        )

        end_time = get_datetime(
            self.left_at
            or self.last_seen_at
            or now_datetime()
        )

        if end_time < joined_at:
            frappe.throw(
                "Invalid timestamps: watch end time is before joined_at."
            )

        duration = time_diff_in_seconds(
            end_time,
            joined_at,
        )

        self.watch_duration_seconds = max(
            int(duration or 0),
            0,
        )

        self.qualified = int(
            self.watch_duration_seconds
            >= QUALIFIED_VIEW_SECONDS
        )
