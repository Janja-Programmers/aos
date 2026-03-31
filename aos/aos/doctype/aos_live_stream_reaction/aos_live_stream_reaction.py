# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.live_analytics_service import LiveAnalyticsService


class AOSLiveStreamReaction(Document):
    def validate(self):
        self._validate_identity()
        self._validate_live_is_active()

    def after_insert(self):
        self._update_live_reaction_count()

    # VALIDATIONS
    def _validate_identity(self):
        """Ensure either user or session_id exists."""
        if not self.user and not self.session_id:
            frappe.throw("Either user or session_id is required")

    def _validate_live_is_active(self):
        live = frappe.db.get_value(
            "AOS Live Stream",
            self.live_stream,
            ["status"],
            as_dict=True,
        )

        if not live:
            frappe.throw("Invalid live stream")

        if live.status != "live":
            frappe.throw("Cannot react on inactive live stream")

    # SIDE EFFECTS
    def _update_live_reaction_count(self):
        """Increment aggregated reaction count."""
        try:
            LiveAnalyticsService.handle_reaction(
                live_id=self.live_stream
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Live reaction analytics update failed",
            )
