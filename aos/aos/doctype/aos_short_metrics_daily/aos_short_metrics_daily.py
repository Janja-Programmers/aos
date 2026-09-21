# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSShortMetricsDaily(Document):
    """Durable per-Short daily counters.

    This DocType deliberately contains metric dimensions only. Marketplace Ads,
    Sellers, countries, locations, and Content Mode classification are separate
    domains and must not be denormalized onto the analytics row.
    """

    def validate(self):
        self._validate_required_fields()
        self._normalize_metric_fields()

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
            "unique_viewers",
            "watch_time_ms",
            "avg_watch_time_ms",
            "rewatches",
            "early_skips",
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
