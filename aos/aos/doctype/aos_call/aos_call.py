from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from aos.services.calls.identifiers import generate_public_call_id
from aos.utils.identifiers import new_prefixed_name

ACTIVE_STATUSES = {"initiated", "ringing", "ongoing"}
TERMINAL_STATUSES = {"ended", "missed", "rejected", "failed", "cancelled"}
VALID_STATUS_TRANSITIONS = {
    "initiated": {"ringing", "ongoing", "rejected", "missed", "failed", "cancelled"},
    "ringing": {"ongoing", "rejected", "missed", "failed", "cancelled"},
    "ongoing": {"ended", "failed"},
    "ended": set(), "missed": set(), "rejected": set(), "failed": set(), "cancelled": set(),
}
IMMUTABLE_FIELDS_AFTER_INSERT = {"public_id", "conversation", "initiator", "call_mode", "room_name", "max_participants"}


class AOSCall(Document):
    def autoname(self):
        self.name = new_prefixed_name("CALL")

    def before_insert(self):
        self.public_id = self.public_id or generate_public_call_id()
        self.room_name = self.room_name or f"call:{frappe.generate_hash(length=32)}"
        self.status = self.status or "initiated"
        self.state_version = max(1, int(self.state_version or 1))
        self.is_active = 1 if self.status in ACTIVE_STATUSES else 0
        self.video_upgrade_status = self.video_upgrade_status or "none"
        self.participant_count = max(0, int(self.participant_count or 0))

    def validate(self):
        if not self.initiator or not frappe.db.exists("User", self.initiator):
            frappe.throw("Initiator is required")
        if self.call_type not in {"audio", "video"}:
            frappe.throw("Invalid call type")
        if self.call_mode not in {"direct", "group"}:
            frappe.throw("Invalid call mode")
        expected_max = 2 if self.call_mode == "direct" else 32
        if int(self.max_participants or 0) != expected_max:
            self.max_participants = expected_max
        if int(self.participant_count or 0) < 0 or int(self.participant_count or 0) > expected_max:
            frappe.throw("Invalid participant count")
        if self.call_mode == "group" and self.conversation:
            frappe.throw("Group calls are not bound to one-to-one conversations")
        if self.call_mode == "group" and (self.video_upgrade_status or "none") != "none":
            frappe.throw("Group calls do not use the direct-call video upgrade flow")
        self._validate_status_transition()
        self._prevent_identity_modification()

    def before_save(self):
        self._handle_status_side_effects()
        self._compute_duration()

    def _validate_status_transition(self):
        if self.is_new():
            return
        old = frappe.db.get_value(self.doctype, self.name, "status")
        if old == self.status:
            return
        if self.status not in VALID_STATUS_TRANSITIONS.get(old, set()):
            frappe.throw(f"Invalid status transition: {old} → {self.status}")

    def _prevent_identity_modification(self):
        if self.is_new():
            return
        original = frappe.db.get_value(self.doctype, self.name, list(IMMUTABLE_FIELDS_AFTER_INSERT), as_dict=True)
        if not original:
            return
        for fieldname in IMMUTABLE_FIELDS_AFTER_INSERT:
            if self.get(fieldname) != original.get(fieldname):
                frappe.throw(f"{frappe.unscrub(fieldname)} cannot be modified once the call is created")

    def _handle_status_side_effects(self):
        now = now_datetime()
        if self.status == "ringing" and not self.ringing_at:
            self.ringing_at = now
        if self.status == "ongoing" and not self.started_at:
            self.started_at = now
        if self.status in TERMINAL_STATUSES:
            self.ended_at = self.ended_at or now
            self.is_active = 0
            self.rtc_missing_since = None
        else:
            self.is_active = 1

    def _compute_duration(self):
        if self.started_at and self.ended_at:
            self.duration = max(0, int((get_datetime(self.ended_at) - get_datetime(self.started_at)).total_seconds()))
