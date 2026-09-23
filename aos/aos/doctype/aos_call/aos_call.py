# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, get_datetime

from aos.services.calls.identifiers import generate_public_call_id


ACTIVE_STATUSES = {"initiated", "ringing", "ongoing"}
TERMINAL_STATUSES = {"ended", "missed", "rejected", "failed", "cancelled"}

VALID_STATUS_TRANSITIONS = {
    "initiated": {"ringing", "ongoing", "rejected", "missed", "failed", "cancelled"},
    "ringing": {"ongoing", "rejected", "missed", "failed", "cancelled"},
    "ongoing": {"ended", "failed"},
    "ended": set(),
    "missed": set(),
    "rejected": set(),
    "failed": set(),
    "cancelled": set(),
}

VALID_VIDEO_UPGRADE_STATUSES = {
    "none",
    "requested",
    "accepted",
    "declined",
    "cancelled",
}


IMMUTABLE_FIELDS_AFTER_INSERT = {
    "public_id",
    "conversation",
    "caller",
    "receiver",
    "room_name",
}


class AOSCall(Document):
    def validate(self):
        self._validate_required_fields()
        self._validate_users()
        self._prevent_self_call()
        self._validate_conversation_participants()
        self._validate_status_transition()
        self._validate_video_upgrade_fields()
        self._validate_single_active_call_per_conversation()

    def before_insert(self):
        self._set_public_id()
        self._set_room_name()
        self._set_initial_state()
        self._set_visibility_defaults()
        self._set_video_upgrade_defaults()

    def before_save(self):
        self._prevent_identity_modification()
        self._increment_state_version_on_document_mutation()
        self._handle_status_side_effects()
        self._compute_duration()

    # Validation
    def _validate_required_fields(self):
        if not self.conversation:
            frappe.throw("Conversation is required")

        if not self.call_type:
            self.call_type = "audio"

        if self.call_type not in ("audio", "video"):
            frappe.throw("Invalid call type")

    def _validate_users(self):
        if not self.caller or not self.receiver:
            frappe.throw("Caller and Receiver are required")

        if not frappe.db.exists("User", self.caller):
            frappe.throw("Caller does not exist")

        if not frappe.db.exists("User", self.receiver):
            frappe.throw("Receiver does not exist")

    def _prevent_self_call(self):
        if self.caller == self.receiver:
            frappe.throw("Cannot call yourself")

    def _validate_conversation_participants(self):
        conv = frappe.db.get_value(
            "AOS Conversation",
            self.conversation,
            ["participant_1", "participant_2"],
            as_dict=True,
        )

        if not conv:
            frappe.throw("Invalid conversation")

        participants = {conv.participant_1, conv.participant_2}

        if self.caller not in participants or self.receiver not in participants:
            frappe.throw("Caller/Receiver must belong to the conversation")

    def _validate_status_transition(self):
        if self.is_new():
            return

        old_status = frappe.db.get_value(self.doctype, self.name, "status")

        if old_status == self.status:
            return

        allowed = VALID_STATUS_TRANSITIONS.get(old_status, set())

        if self.status not in allowed:
            frappe.throw(f"Invalid status transition: {old_status} → {self.status}")

    def _validate_video_upgrade_fields(self):
        if not self.video_upgrade_status:
            self.video_upgrade_status = "none"

        if self.video_upgrade_status not in VALID_VIDEO_UPGRADE_STATUSES:
            frappe.throw("Invalid video upgrade status")

        if (
            self.video_upgrade_requested_by
            and self.video_upgrade_requested_by not in (self.caller, self.receiver)
        ):
            frappe.throw("Video upgrade requester must be a call participant")

        if self.video_upgrade_status == "requested":
            if self.call_type != "audio":
                frappe.throw("Video upgrade can only be requested for audio calls")

            if not self.video_upgrade_requested_by:
                frappe.throw("Video upgrade requester is required")

            if not self.video_upgrade_requested_at:
                frappe.throw("Video upgrade request time is required")

            if self.video_upgrade_responded_at:
                frappe.throw("Pending video upgrade cannot have response time")

        elif self.video_upgrade_status == "none":
            # Clean stale metadata when there is no active upgrade request/history.
            self.video_upgrade_requested_by = None
            self.video_upgrade_requested_at = None
            self.video_upgrade_responded_at = None

        elif self.video_upgrade_status in {"accepted", "declined", "cancelled"}:
            if not self.video_upgrade_requested_by:
                frappe.throw("Video upgrade requester is required")

            if not self.video_upgrade_requested_at:
                frappe.throw("Video upgrade request time is required")

            if not self.video_upgrade_responded_at:
                frappe.throw("Video upgrade response time is required")

        if self.call_type == "video" and self.video_upgrade_status == "requested":
            frappe.throw("Video call cannot have a pending upgrade request")

    def _validate_single_active_call_per_conversation(self):
        """Enforce the legacy method's invariant across both participants.

        Calls are one-to-one in the current AOS model. A user may therefore
        have at most one active/ringing call, even when another conversation
        is involved. Endpoint code additionally takes deterministic locks;
        this document guard protects Desk/internal writes.
        """
        if self.status not in ACTIVE_STATUSES:
            return

        users = tuple(sorted({self.caller, self.receiver}))
        existing = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Call`
            WHERE name != %(name)s
              AND is_active = 1
              AND status IN ('initiated', 'ringing', 'ongoing')
              AND (caller IN %(users)s OR receiver IN %(users)s)
            LIMIT 1
            """,
            {"name": self.name or "", "users": users},
            as_dict=True,
        )

        if existing:
            frappe.throw("A participant already has an active call")

    def _prevent_identity_modification(self):
        if self.is_new():
            return

        original = frappe.db.get_value(
            self.doctype,
            self.name,
            list(IMMUTABLE_FIELDS_AFTER_INSERT),
            as_dict=True,
        )

        if not original:
            return

        for fieldname in IMMUTABLE_FIELDS_AFTER_INSERT:
            if self.get(fieldname) != original.get(fieldname):
                frappe.throw(
                    f"{frappe.unscrub(fieldname)} cannot be modified once the call is created"
                )

    # State setup / side effects
    def _set_initial_state(self):
        if not self.status:
            self.status = "initiated"

        self.is_active = 1 if self.status in ACTIVE_STATUSES else 0
        self.state_version = max(1, int(self.state_version or 1))

    def _set_visibility_defaults(self):
        """
        New calls should be visible to both participants by default.
        """
        if self.visible_to_caller is None:
            self.visible_to_caller = 1

        if self.visible_to_receiver is None:
            self.visible_to_receiver = 1

    def _set_video_upgrade_defaults(self):
        """
        New calls should not have a video upgrade request by default.
        """
        if not self.video_upgrade_status:
            self.video_upgrade_status = "none"

    def _set_public_id(self):
        if not self.public_id:
            self.public_id = generate_public_call_id()

    def _set_room_name(self):
        if not self.room_name:
            # RTC room names are server-owned opaque values and are independent
            # from client-visible call IDs. Clients never choose or receive them.
            self.room_name = f"call:{frappe.generate_hash(length=32)}"

    def _increment_state_version_on_document_mutation(self):
        if self.is_new():
            return

        original = frappe.db.get_value(
            self.doctype,
            self.name,
            [
                "status",
                "call_type",
                "video_upgrade_status",
                "video_upgrade_requested_by",
                "video_upgrade_requested_at",
                "video_upgrade_responded_at",
            ],
            as_dict=True,
        )
        if not original:
            return

        changed = any(
            self.get(fieldname) != original.get(fieldname)
            for fieldname in original
        )
        if changed:
            self.state_version = max(1, int(self.state_version or 1)) + 1

    def _handle_status_side_effects(self):
        now = now_datetime()

        # Ringing
        if self.status == "ringing" and not self.ringing_at:
            self.ringing_at = now

        # Started
        if self.status == "ongoing" and not self.started_at:
            self.started_at = now

        # Terminal states. Queue room cleanup only on the transition; an
        # unrelated later Desk save must not resurrect already-completed work.
        if self.status in TERMINAL_STATUSES:
            if not self.ended_at:
                self.ended_at = now

            self.is_active = 0
            old_status = None if self.is_new() else frappe.db.get_value(
                self.doctype, self.name, "status"
            )
            if self.is_new() or old_status not in TERMINAL_STATUSES:
                self.room_cleanup_pending = 1
            self.rtc_missing_since = None

        elif self.status in ACTIVE_STATUSES:
            self.is_active = 1
            self.room_cleanup_pending = 0

    def _compute_duration(self):
        if not self.started_at or not self.ended_at:
            return

        started_at = get_datetime(self.started_at)
        ended_at = get_datetime(self.ended_at)

        delta = ended_at - started_at
        self.duration = max(0, int(delta.total_seconds()))
