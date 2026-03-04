# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now


class AOSAdReport(Document):
    def validate(self):
        self.prevent_duplicate_reports()

    def on_update(self):
        self.apply_admin_action()

    def prevent_duplicate_reports(self):
        if not self.ad or not self.reported_by:
            return

        exists = frappe.db.exists(
            "AOS Ad Report",
            {
                "ad": self.ad,
                "reported_by": self.reported_by,
                "name": ["!=", self.name],
            },
        )

        if exists:
            frappe.throw("You have already reported this ad.")

    def apply_admin_action(self):
        if not self.admin_action:
            return

        if self.status != "Resolved":
            return

        # Suspend Ad
        if self.admin_action == "Ad Suspended":
            frappe.db.set_value(
                "AOS Ad",
                self.ad,
                "status",
                "Suspended",
                update_modified=False,
            )

        # Suspend Seller
        elif self.admin_action == "Seller Suspended":
            frappe.db.set_value(
                "AOS Seller",
                self.seller,
                "status",
                "Suspended",
                update_modified=False,
            )

        # Seller Warned → no system change
        elif self.admin_action == "Seller Warned":
            pass

        # Set review metadata
        if not self.reviewed_by:
            self.reviewed_by = frappe.session.user

        if not self.reviewed_on:
            self.reviewed_on = now()
