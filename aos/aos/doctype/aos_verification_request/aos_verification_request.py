# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now

from aos.services.notification_service import NotificationService
from aos.services.sellers.policy import sync_verified_business_profile


class AOSVerificationRequest(Document):
    def validate(self):
        self._validate_request()
        self._stamp_verification_metadata()

    def on_update(self):
        self._sync_verification_result()

    def _validate_request(self):
        if not self.user:
            frappe.throw(_("User is required."))

        if not frappe.db.exists("User", self.user):
            frappe.throw(_("User does not exist."))

        if not frappe.db.exists("AOS Profile", self.user):
            frappe.throw(_("AOS Profile does not exist for this user."))

        if self.verification_type not in ["Business", "Individual"]:
            frappe.throw(_("Invalid verification type."))

        if self.status == "Rejected" and not self.rejection_reason:
            frappe.throw(_("Rejection reason is required when request is rejected."))

        if self.verification_type == "Business":
            self._validate_business_request()

        elif self.verification_type == "Individual":
            self._validate_individual_request()

    def _validate_business_request(self):
        required_fields = {
            "business_name": _("Business Name"),
            "business_type": _("Business Type"),
            "business_category": _("Business Category"),
            "business_phone_number": _("Business Phone Number"),
            "business_email": _("Business Email"),
            "business_address": _("Business Address"),
        }

        for fieldname, label in required_fields.items():
            if not self.get(fieldname):
                frappe.throw(_("{0} is required for business verification.").format(label))

        if not frappe.db.exists("AOS Seller", {"user": self.user}):
            frappe.throw(_("AOS Seller is required for business verification."))

    def _validate_individual_request(self):
        required_fields = {
            "legal_name": _("Legal Name"),
            "phone_number": _("Phone Number"),
        }

        for fieldname, label in required_fields.items():
            if not self.get(fieldname):
                frappe.throw(_("{0} is required for individual verification.").format(label))

    def _stamp_verification_metadata(self):
        if frappe.session.user == "Guest":
            return

        previous = self.get_doc_before_save()

        if not previous:
            return

        if previous.status == self.status:
            return

        if self.status not in ["Approved", "Rejected", "Revoked"]:
            return

        self.verified_by = frappe.session.user
        self.verified_on = now()

    def _sync_verification_result(self):
        previous = self.get_doc_before_save()

        if previous and previous.status == self.status:
            return

        if self.status == "Approved":
            self._approve_profile()
            self._sync_business_fields_if_needed()
            self._notify_approved()

        elif self.status == "Rejected":
            self._notify_rejected()

        elif self.status == "Revoked":
            self._revoke_profile()

    def _approve_profile(self):
        profile = frappe.get_doc("AOS Profile", self.user)
        profile.is_verified = 1
        profile.verified_by = self.verified_by
        profile.verified_on = self.verified_on
        profile.save(ignore_permissions=True)

    def _revoke_profile(self):
        profile = frappe.get_doc("AOS Profile", self.user)
        profile.is_verified = 0
        profile.verified_by = None
        profile.verified_on = None
        profile.save(ignore_permissions=True)

    def _sync_business_fields_if_needed(self):
        if self.verification_type != "Business":
            return

        sync_verified_business_profile(
            user=self.user,
            business_category=self.business_category,
            source="verification_approved",
        )

    def _notify_approved(self):
        try:
            NotificationService.notify_verification_approved(user=self.user)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "AOS Verification Approved Notification Failed",
            )

    def _notify_rejected(self):
        try:
            NotificationService.notify_verification_rejected(user=self.user)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "AOS Verification Rejected Notification Failed",
            )
