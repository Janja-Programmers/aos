# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class AOSLiveStreamAd(Document):
    def validate(self):
        self._validate_live_exists()
        self._validate_ad_exists()
        self._validate_ad_belongs_to_seller()
        self._validate_ad_is_active()
        self._validate_same_country()
        self._validate_no_duplicate()

    def before_save(self):
        self._handle_pinning()

    # VALIDATIONS
    def _validate_live_exists(self):
        live = frappe.db.get_value(
            "AOS Live Stream",
            self.live_stream,
            ["seller", "country", "status"],
            as_dict=True,
        )

        if not live:
            frappe.throw("Invalid live stream")

        self._live = live

    def _validate_ad_exists(self):
        ad = frappe.db.get_value(
            "AOS Ad",
            self.ad,
            ["seller", "status", "country"],
            as_dict=True,
        )

        if not ad:
            frappe.throw("Invalid ad")

        self._ad = ad

    def _validate_ad_belongs_to_seller(self):
        if self._ad.seller != self._live.seller:
            frappe.throw("Ad must belong to the same seller as the live stream")

    def _validate_ad_is_active(self):
        if self._ad.status != "Active":
            frappe.throw("Only active ads can be attached to a live stream")

    def _validate_same_country(self):
        if self._ad.country != self._live.country:
            frappe.throw("Ad must belong to the same country as the live stream")

    def _validate_no_duplicate(self):
        existing = frappe.db.exists(
            "AOS Live Stream Ad",
            {
                "live_stream": self.live_stream,
                "ad": self.ad,
                "name": ["!=", self.name or ""],
            },
        )

        if existing:
            frappe.throw("Ad already attached to this live stream")

    # SIDE EFFECTS
    def _handle_pinning(self):
        """
        Ensure only one pinned ad per live stream.
        """
        if not self.is_pinned:
            return

        frappe.db.sql(
            """
            UPDATE `tabAOS Live Stream Ad`
            SET is_pinned = 0
            WHERE live_stream = %s
              AND name != %s
            """,
            (self.live_stream, self.name or ""),
        )
