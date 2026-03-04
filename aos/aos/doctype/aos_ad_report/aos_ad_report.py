# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now


class AOSAdReport(Document):
    def validate(self):
        self.prevent_duplicate_reports()
        self.validate_admin_action()

    def on_update(self):
        self.apply_admin_action()
        self._stamp_review_metadata()

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

    def validate_admin_action(self):
        if self.admin_action and self.status != "Resolved":
            frappe.throw("Admin action can only be applied when status is Resolved.")

    def apply_admin_action(self):
        if not self.admin_action:
            return

        previous = self.get_doc_before_save()

        # Only run moderation if admin_action changed
        if previous and previous.admin_action == self.admin_action:
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

    def _stamp_review_metadata(self):
        """Stamp moderation metadata when report is reviewed."""
        if frappe.session.user == "Guest":
            return

        previous = self.get_doc_before_save()

        if not previous or (
            previous.status != self.status
            or previous.admin_action != self.admin_action
        ):
            self.reviewed_by = frappe.session.user
            self.reviewed_on = now()
