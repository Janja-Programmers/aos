# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSShortMetricsDaily(Document):
    def validate(self):
        self._validate_required_fields()
        self._normalize_metric_fields()

    def before_insert(self):
        self._enrich_from_short()

    def before_save(self):
        self._enrich_from_short()

    def _validate_required_fields(self):
        if not self.short:
            frappe.throw("Short is required")

        if not self.date:
            frappe.throw("Date is required")

    def _normalize_metric_fields(self):
        """Keep analytics rows safe even when inserted by scripts/imports."""
        int_fields = (
            "impressions",
            "views",
            "watch_time_ms",
            "avg_watch_time_ms",
            "likes",
            "comments",
            "shares",
            "saves",
            "downloads",
            "reposts",
        )

        for fieldname in int_fields:
            value = getattr(self, fieldname, 0) or 0
            try:
                value = int(value)
            except Exception:
                value = 0
            setattr(self, fieldname, max(value, 0))

        try:
            self.completion_rate = max(float(self.completion_rate or 0), 0)
        except Exception:
            self.completion_rate = 0

    def _enrich_from_short(self):
        """Denormalize fields from Short."""
        short = frappe.db.get_value(
            "AOS Short",
            self.short,
            ["ad", "seller", "country"],
            as_dict=True,
        )

        if not short:
            frappe.throw("Short not found")

        self.ad = short.ad
        self.seller = short.seller
        self.country = short.country
