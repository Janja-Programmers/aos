# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now


class AOSAdReport(Document):
    def validate(self):
        self.prevent_duplicate_reports()
        self.validate_admin_action()

    def before_save(self):
        self._stamp_review_metadata()

    def after_insert(self):
        self._recompute_ad_total_reports()

    def on_update(self):
        self.apply_admin_action()
        self._recompute_ad_total_reports_if_needed()

    def on_trash(self):
        self._recompute_ad_total_reports()

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
        if not self.admin_action:
            return

        if self.status != "Resolved":
            frappe.throw("Admin action can only be applied when status is Resolved.")

    def apply_admin_action(self):
        if not self.admin_action:
            return

        previous = self.get_doc_before_save()

        # Only run moderation when action changes
        if previous and previous.admin_action == self.admin_action:
            return

        if self.status != "Resolved":
            return

        # Suspend Ad
        if self.admin_action == "Suspended Ad":
            frappe.db.set_value(
                "AOS Ad",
                self.ad,
                "status",
                "Suspended",
                update_modified=False,
            )

        # Suspend Seller
        elif self.admin_action == "Suspended Seller":
            frappe.db.set_value(
                "AOS Seller",
                self.seller,
                "status",
                "Suspended",
                update_modified=False,
            )

        # Warn Seller (no system action)
        elif self.admin_action == "Warn Seller":
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

    def _recompute_ad_total_reports(self):
        """Recompute total_reports on the related Ad."""
        if not self.ad:
            return

        total = frappe.db.count(
            "AOS Ad Report",
            {
                "ad": self.ad,
                "status": ["!=", "Rejected"],
            },
        )

        frappe.db.set_value(
            "AOS Ad",
            self.ad,
            "total_reports",
            int(total or 0),
            update_modified=False,
        )

    def _recompute_ad_total_reports_if_needed(self):
        """Only recompute when status changes."""
        previous = self.get_doc_before_save()

        if not previous:
            self._recompute_ad_total_reports()
            return

        if previous.status != self.status:
            self._recompute_ad_total_reports()
