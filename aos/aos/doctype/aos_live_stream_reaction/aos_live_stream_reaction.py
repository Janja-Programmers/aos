# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

LIVE_STATUS = "live"

VALID_REACTION_TYPES = {
    "like",
    "fire",
    "clap",
    "love",
    "wow",
}


class AOSLiveStreamReaction(Document):
    def validate(self):
        self._validate_required_fields()
        self._validate_user()
        self._validate_reaction_type()
        self._validate_live_is_active()

    # VALIDATIONS
    def _validate_required_fields(self):
        if not self.live_stream:
            frappe.throw("Live stream is required.")

        if not self.user:
            frappe.throw("User is required.")

        if not self.reaction_type:
            frappe.throw("Reaction type is required.")

    def _validate_user(self):
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

    def _validate_reaction_type(self):
        if self.reaction_type not in VALID_REACTION_TYPES:
            frappe.throw("Invalid reaction type.")

    def _validate_live_is_active(self):
        live = frappe.db.get_value(
            "AOS Live Stream",
            self.live_stream,
            ["name", "status", "is_active"],
            as_dict=True,
        )

        if not live:
            frappe.throw("Invalid live stream.")

        if live.status != LIVE_STATUS or not live.is_active:
            frappe.throw("Cannot react on inactive live stream.")

