# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSShortMetricsDaily(Document):
    def validate(self):
        self._validate_required_fields()

    def before_insert(self):
        self._enrich_from_short()

    def _validate_required_fields(self):
        if not self.short:
            frappe.throw("Short is required")

        if not self.date:
            frappe.throw("Date is required")

    def _enrich_from_short(self):
        """Denormalize fields from Short"""
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
