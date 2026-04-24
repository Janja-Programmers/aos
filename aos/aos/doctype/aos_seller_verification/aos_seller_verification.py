# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now

from aos.services.notification_service import NotificationService


class AOSSellerVerification(Document):
    def validate(self):
        self._stamp_verification_metadata()

    def on_update(self):
        self._sync_seller()

    def _stamp_verification_metadata(self):
        """
        Stamp verification metadata when status changes.
        Also trigger notifications.
        """
        if frappe.session.user == "Guest":
            return

        previous = self.get_doc_before_save()

        if not previous:
            return

        # No change
        if previous.status == self.status:
            return

        # Stamp metadata
        if self.status not in ["Approved", "Rejected"]:
            return

        self.verified_by = frappe.session.user
        self.verified_on = now()

        # NOTIFICATIONS
        try:
            seller_doc = frappe.get_doc("AOS Seller", self.seller)
            seller_user = seller_doc.user

            if self.status == "Approved":
                NotificationService.notify_verification_approved(
                    user=seller_user
                )

            elif self.status == "Rejected":
                NotificationService.notify_verification_rejected(
                    user=seller_user
                )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "AOS Verification Notification Failed",
            )

    def _sync_seller(self):
        """Sync verification result with seller profile."""
        if not self.seller:
            return

        previous = self.get_doc_before_save()

        # Skip if no status change
        if previous and previous.status == self.status:
            return

        seller = frappe.get_doc("AOS Seller", self.seller)

        if self.status == "Approved":
            seller.is_verified = 1
            seller.seller_type = "Business"
            seller.shop_name = self.business_name
            seller.category = self.business_category
            seller.physical_address = self.physical_address
            seller.verified_on = self.verified_on
            seller.verified_by = self.verified_by

        elif self.status == "Rejected":
            seller.is_verified = 0
            seller.seller_type = "Individual"

        seller.save(ignore_permissions=True)
