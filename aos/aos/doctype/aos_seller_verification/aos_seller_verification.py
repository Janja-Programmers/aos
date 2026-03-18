# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now


class AOSSellerVerification(Document):
    def validate(self):
        self._stamp_verification_metadata()

    def on_update(self):
        self._sync_seller()

    def _stamp_verification_metadata(self):
        """Stamp verification metadata when status changes."""
        if frappe.session.user == "Guest":
            return

        previous = self.get_doc_before_save()

        if not previous or previous.status != self.status:
            if self.status == "Approved":
                self.verified_by = frappe.session.user
                self.verified_on = now()

            elif self.status == "Rejected":
                self.verified_by = frappe.session.user
                self.verified_on = now()

    def _sync_seller(self):
        """Sync verification result with seller profile."""
        if not self.seller:
            return

        seller = frappe.get_doc("AOS Seller", self.seller)

        if self.status == "Approved":
            seller.is_verified = 1
            seller.seller_type = "Business"
            seller.verified_on = self.verified_on
            seller.verified_by = self.verified_by

        elif self.status == "Rejected":
            seller.is_verified = 0
            seller.seller_type = "Individual"
            seller.verified_on = None
            seller.verified_by = None

        seller.save(ignore_permissions=True)
