# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe

from aos.services.shorts.policy import can_view
from frappe.model.document import Document


class AOSShortEvent(Document):
    def validate(self):
        self._set_identity()
        self._validate_short()
        self._validate_event_type()

    def before_insert(self):
        self._enrich_from_short()

    def _set_identity(self):
        """Ensure user or session exists"""
        if not self.user:
            self.user = frappe.session.user

        if self.user == "Guest" and not self.session_id:
            frappe.throw("Session ID required for guest users")

    def _validate_short(self):
        if not self.short:
            frappe.throw("Short is required")

        short = frappe.db.get_value(
            "AOS Short",
            self.short,
            ["name", "owner", "status", "visibility_status", "approval_status", "audience"],
            as_dict=True,
        )

        if not short:
            frappe.throw("Short not found")

        if short.status != "ready":
            frappe.throw("Short is not available")

        if short.visibility_status != "visible":
            frappe.throw("Short is not visible")
        viewer = self.user if self.user and self.user != "Guest" else None
        if not can_view(short, viewer=viewer):
            frappe.throw("Short is not available")

    def _validate_event_type(self):
        allowed = {
            "impression",
            "play",
            "pause",
            "progress",
            "complete",
            "like",
            "comment",
            "open_ad",
            "open_seller",
            "follow_click",
            "share",
            "save",
            "download",
            "repost",
        }

        if self.event_type not in allowed:
            frappe.throw("Invalid event type")

    def _enrich_from_short(self):
        """Denormalize fields from Short"""
        short = frappe.db.get_value(
            "AOS Short",
            self.short,
            ["ad", "seller", "country"],
            as_dict=True,
        )

        if not short:
            return

        self.ad = short.ad
        self.seller = short.seller
        self.country = short.country
