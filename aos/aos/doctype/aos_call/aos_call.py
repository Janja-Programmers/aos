# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, get_datetime


ACTIVE_STATUSES = {"initiated", "ringing", "ongoing"}
TERMINAL_STATUSES = {"ended", "missed", "rejected", "failed", "cancelled"}


class AOSCall(Document):
    def validate(self):
        self._validate_users()
        self._validate_conversation_participants()
        self._prevent_self_call()
        self._validate_status_transition()
        self._validate_single_active_call_per_conversation()

    def before_insert(self):
        self._set_room_name()
        self._set_initial_state()

    def before_save(self):
        self._handle_status_side_effects()
        self._compute_duration()

    def _validate_users(self):
        if not self.caller or not self.receiver:
            frappe.throw("Caller and Receiver are required")

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

        valid_transitions = {
            "initiated": {"ringing", "missed", "failed", "cancelled"},
            "ringing": {"ongoing", "rejected", "missed", "failed", "cancelled"},
            "ongoing": {"ended", "failed"},
            "ended": set(),
            "missed": set(),
            "rejected": set(),
            "failed": set(),
            "cancelled": set(),
        }

        allowed = valid_transitions.get(old_status, set())

        if self.status not in allowed:
            frappe.throw(f"Invalid status transition: {old_status} → {self.status}")

    def _validate_single_active_call_per_conversation(self):
        if self.status not in ACTIVE_STATUSES:
            return

        existing = frappe.db.exists(
            "AOS Call",
            {
                "conversation": self.conversation,
                "is_active": 1,
                "name": ["!=", self.name or ""],
            },
        )

        if existing:
            frappe.throw("There is already an active call for this conversation")

    def _set_initial_state(self):
        if not self.status:
            self.status = "initiated"

        self.is_active = 1

    def _set_room_name(self):
        if not self.room_name:
            self.room_name = f"call:{self.conversation}"

    def _handle_status_side_effects(self):
        now = now_datetime()

        # Ringing
        if self.status == "ringing" and not self.ringing_at:
            self.ringing_at = now

        # Started
        if self.status == "ongoing" and not self.started_at:
            self.started_at = now

        # Terminal states
        if self.status in TERMINAL_STATUSES:
            if not self.ended_at:
                self.ended_at = now

            self.is_active = 0

    def _compute_duration(self):
        if not self.started_at or not self.ended_at:
            return

        started_at = get_datetime(self.started_at)
        ended_at = get_datetime(self.ended_at)

        delta = ended_at - started_at
        self.duration = int(delta.total_seconds())
